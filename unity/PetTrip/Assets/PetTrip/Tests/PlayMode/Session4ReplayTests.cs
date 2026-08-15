using System.IO;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace PetTrip.Tests
{
    /// <summary>
    /// 会话4 阶段二（服务重启 + 离线重放后）：仅从既有 artifact 重建的 v2 快照
    /// 必须原样恢复小窝——位置与类型不变，交互点仍在，全程无新模型请求。
    /// </summary>
    public sealed class Session4ReplayTests
    {
        [UnityTest]
        public System.Collections.IEnumerator ReplayedSnapshotRestoresShelterWithoutModelCalls()
        {
            SceneManager.LoadScene("Session2Beach");
            yield return null;

            var loader = Object.FindFirstObjectByType<HttpSceneSnapshotLoader>();
            var deadline = Time.realtimeSinceStartup + 15f;
            while (loader == null || (!loader.IsLoaded && string.IsNullOrEmpty(loader.LoadError)))
            {
                if (Time.realtimeSinceStartup > deadline) break;
                yield return new WaitForSeconds(0.2f);
            }

            Assert.IsNotNull(loader, "HttpSceneSnapshotLoader not found in Session2Beach scene.");
            Assert.IsNull(loader.LoadError, "HTTP load failed: " + loader.LoadError);
            Assert.IsTrue(loader.IsLoaded, "Replayed snapshot did not load. Is the restarted service running?");

            var snapshot = loader.LoadedSnapshot;
            Assert.AreEqual("0.2", snapshot.schema_version, "重放快照必须是 v0.2。");

            var slot = snapshot.build_slots[0];
            Assert.AreEqual("small_shelter", slot.id);
            Assert.AreEqual("small_shelter", slot.placed_prefab, "重放后放置类型不变。");
            Assert.AreEqual(430f, slot.position.x, "重放后小窝位置 x 不变。");
            Assert.AreEqual(96f, slot.position.y, "重放后小窝位置 y 不变。");

            var shelter = loader.GeneratedScene.transform.Find("small_shelter");
            Assert.IsNotNull(shelter, "重放加载后必须渲染小窝。");
            Assert.AreEqual(30, shelter.GetComponent<SpriteRenderer>().sortingOrder);

            Assert.IsNotNull(loader.GeneratedScene.transform.Find("pet_wave"), "pet_wave 交互点必须随快照恢复。");
            Assert.IsNotNull(loader.GeneratedScene.transform.Find("lighthouse"), "lighthouse 图层必须随快照恢复。");

            var path = Path.GetFullPath(Path.Combine(Application.dataPath, "../TestArtifacts/Session4/unity-replay-screenshot.png"));
            Session1ScreenshotCapture.Capture(Camera.main, path);
            Assert.IsTrue(File.Exists(path));
            Debug.Log("PETTRIP_SESSION4_REPLAY_OK path=" + path);
        }
    }
}
