using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace PetTrip.Tests
{
    /// <summary>
    /// 会话4 阶段一：加载统一输入重建的 v0.2 快照（槽位未放置）-> 宠物区内移动与
    /// 越界拒绝 -> 触发 pet_wave -> 数据层放置 small_shelter（未允许 Prefab 被拒）
    /// -> 上传 v2 -> 清空重载 -> 小窝位置与类型不变 -> 截图 -> 报告回传。
    /// </summary>
    public sealed class Session4InteractionTests
    {
        private const string BaseUrl = "http://127.0.0.1:8000";

        [UnityTest]
        public IEnumerator InteractionFlowUploadsV2AndReport()
        {
            SceneManager.LoadScene("Session2Beach");
            yield return null;

            var loader = UnityEngine.Object.FindFirstObjectByType<HttpSceneSnapshotLoader>();
            yield return WaitForLoad(loader);
            Assert.IsNotNull(loader, "HttpSceneSnapshotLoader not found in Session2Beach scene.");
            Assert.IsNull(loader.LoadError, "HTTP load failed: " + loader.LoadError);
            Assert.IsTrue(loader.IsLoaded, "Snapshot did not load in time. Is the session4 service running on 127.0.0.1:8000?");

            var snapshot = loader.LoadedSnapshot;
            Assert.AreEqual("0.2", snapshot.schema_version, "统一输入重建的快照必须是 v0.2。");
            var slot = snapshot.build_slots[0];
            Assert.IsTrue(string.IsNullOrEmpty(slot.placed_prefab), "初始快照槽位必须未放置。");
            Assert.IsNull(loader.GeneratedScene.transform.Find("small_shelter"), "未放置时不得渲染小窝。");

            var pet = loader.GeneratedScene.transform.Find("pet");
            Assert.IsNotNull(pet, "pet layer is missing.");
            var movement = pet.GetComponent<PetMovement>();
            Assert.IsNotNull(movement, "PetMovement must be attached to the pet layer.");

            var beforeMove = pet.localPosition;
            Assert.IsTrue(movement.TryMoveTo(NewPoint(100, 100)), "区内移动必须被接受。");
            Assert.AreNotEqual(beforeMove, pet.localPosition, "接受移动后宠物位置必须变化。");
            var afterMove = pet.localPosition;
            Assert.IsFalse(movement.TryMoveTo(NewPoint(480, 200)), "活动区外的目标必须被拒绝。");
            Assert.AreEqual(afterMove, pet.localPosition, "被拒绝的移动不得改变位置。");
            Assert.IsTrue(movement.LastMoveWasRejected);

            var wave = loader.GeneratedScene.transform.Find("pet_wave")?.GetComponent<PetWaveInteraction>();
            Assert.IsNotNull(wave, "pet_wave interaction is missing.");
            wave.Trigger();
            Assert.IsTrue(wave.WasTriggered, "pet_wave must be triggerable.");

            var builder = UnityEngine.Object.FindFirstObjectByType<SnapshotSceneBuilder>();
            var v2 = SnapshotSceneBuilder.PlacePrefab(snapshot, "small_shelter", "small_shelter");
            Assert.AreEqual("0.2", v2.schema_version);
            Assert.AreEqual("small_shelter", v2.build_slots[0].placed_prefab);
            Assert.Throws<ArgumentException>(
                () => SnapshotSceneBuilder.PlacePrefab(snapshot, "small_shelter", "rocket"),
                "未 allowed_prefabs 允许的 Prefab 必须被拒绝。");

            string runId = null;
            yield return GetRunId(value => runId = value);
            Assert.IsFalse(string.IsNullOrEmpty(runId), "无法从服务获取 run_id。");

            var client = new GameObject("Session4Client").AddComponent<Session4ServiceClient>();
            var uploadOk = false;
            yield return client.UploadSnapshotV2(runId, SnapshotSceneBuilder.SerializeSnapshot(v2), ok => uploadOk = ok);
            Assert.IsTrue(uploadOk, "v2 上传失败: " + client.LastError);

            // 清空并仅用 v2 重载（服务端 active 已切到 scene-snapshot-v2.json）
            yield return loader.Load();
            Assert.IsNull(loader.LoadError, "v2 reload failed: " + loader.LoadError);
            var reloaded = loader.LoadedSnapshot;
            Assert.AreEqual("0.2", reloaded.schema_version);
            var reloadedSlot = reloaded.build_slots[0];
            Assert.AreEqual("small_shelter", reloadedSlot.placed_prefab, "重载后放置类型不变。");
            Assert.AreEqual(slot.position.x, reloadedSlot.position.x, "重载后槽位位置 x 不变。");
            Assert.AreEqual(slot.position.y, reloadedSlot.position.y, "重载后槽位位置 y 不变。");

            var shelter = loader.GeneratedScene.transform.Find("small_shelter");
            Assert.IsNotNull(shelter, "v2 重载后必须渲染小窝。");
            Assert.IsNotNull(shelter.GetComponent<SpriteRenderer>().sprite, "小窝 sprite 必须可用。");

            var screenshot = Path.GetFullPath(Path.Combine(Application.dataPath, "../TestArtifacts/Session4/unity-screenshot.png"));
            Session1ScreenshotCapture.Capture(Camera.main, screenshot);
            Assert.IsTrue(File.Exists(screenshot));

            var checks = new List<(string name, bool passed, string detail)>
            {
                ("pet_move_in_zone_accepted", true, "moved to 100,100"),
                ("pet_move_outside_zone_rejected", true, "480,200 rejected"),
                ("pet_wave_triggered", true, "interaction fired"),
                ("shelter_placed", true, "small_shelter into slot"),
                ("unallowed_prefab_rejected", true, "rocket rejected"),
                ("v2_reloaded", true, "position and type unchanged"),
            };
            var reportOk = false;
            yield return client.UploadReport(checks, screenshot, ok => reportOk = ok);
            Assert.IsTrue(reportOk, "报告上传失败: " + client.LastError);

            Debug.Log("PETTRIP_SESSION4_INTERACTION_OK run_id=" + runId);
        }

        private static PixelPoint NewPoint(float x, float y) => new() { x = x, y = y };

        private static IEnumerator WaitForLoad(HttpSceneSnapshotLoader loader)
        {
            var deadline = Time.realtimeSinceStartup + 15f;
            while (loader == null || (!loader.IsLoaded && string.IsNullOrEmpty(loader.LoadError)))
            {
                if (Time.realtimeSinceStartup > deadline) yield break;
                yield return new WaitForSeconds(0.2f);
            }
        }

        private static IEnumerator GetRunId(Action<string> onComplete)
        {
            using (var request = UnityWebRequest.Get(BaseUrl + "/run-id"))
            {
                request.timeout = 10;
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                {
                    Debug.LogError("run-id fetch failed: " + request.error);
                    onComplete?.Invoke(null);
                    yield break;
                }
                var payload = JsonUtility.FromJson<RunIdPayload>(request.downloadHandler.text);
                onComplete?.Invoke(payload.run_id);
            }
        }

        [Serializable]
        private sealed class RunIdPayload
        {
            public string run_id;
        }
    }
}
