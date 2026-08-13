using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;

namespace PetTrip
{
    /// <summary>
    /// 会话2：只经 HTTP 取得 SceneSnapshot 与素材并构建场景（不读取本地文件）。
    /// 协程：GET /snapshot -> 校验 -> 下载 asset PNG -> SnapshotSceneBuilder.Build。
    /// 错误通过 LoadError 属性暴露，供测试断言（避开 yield 不能处于带 catch 的 try 的限制）。
    /// </summary>
    public sealed class HttpSceneSnapshotLoader : MonoBehaviour
    {
        [SerializeField] private SnapshotSceneBuilder sceneBuilder;
        [SerializeField] private HttpSpriteProvider spriteProvider;
        [SerializeField] private string baseUrl = "http://127.0.0.1:8000";

        public SceneSnapshot LoadedSnapshot { get; private set; }
        public GameObject GeneratedScene { get; private set; }
        public bool IsLoaded { get; private set; }
        public string LoadError { get; private set; }

        private IEnumerator Start() => Load();

        public IEnumerator Load()
        {
            LoadError = null;
            IsLoaded = false;

            if (sceneBuilder == null)
            {
                Fail("SnapshotSceneBuilder is not assigned.");
                yield break;
            }
            if (spriteProvider == null)
            {
                Fail("HttpSpriteProvider is not assigned.");
                yield break;
            }

            string json;
            using (var request = UnityWebRequest.Get(baseUrl + "/snapshot"))
            {
                request.timeout = 10;
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                {
                    Fail("HTTP snapshot fetch failed: " + request.error);
                    yield break;
                }
                json = request.downloadHandler.text;
            }

            SceneSnapshot snapshot;
            try
            {
                snapshot = JsonUtility.FromJson<SceneSnapshot>(json);
                SceneSnapshotValidator.Validate(snapshot);
            }
            catch (System.Exception exception)
            {
                Fail("Snapshot parse or validate failed: " + exception.Message);
                yield break;
            }

            var assetIds = new List<string>();
            foreach (var layer in snapshot.layers) assetIds.Add(layer.asset_id);
            foreach (var slot in snapshot.build_slots)
                foreach (var prefab in slot.allowed_prefabs)
                    assetIds.Add(prefab);

            yield return spriteProvider.LoadAll(baseUrl, assetIds);
            if (!string.IsNullOrEmpty(spriteProvider.LastError))
            {
                Fail(spriteProvider.LastError);
                yield break;
            }

            try
            {
                GeneratedScene = sceneBuilder.Build(snapshot);
                LoadedSnapshot = snapshot;
                IsLoaded = true;
                Debug.Log("PETTRIP_HTTP_SNAPSHOT_LOAD_OK schema=" + snapshot.schema_version +
                          " scene=" + snapshot.scene_id + " layers=" + snapshot.layers.Length +
                          " assets=" + spriteProvider.LoadedAssetIds.Count);
            }
            catch (System.Exception exception)
            {
                Fail("Scene build failed: " + exception.Message);
            }
        }

        private void Fail(string reason)
        {
            LoadError = reason;
            Debug.LogError("PETTRIP_HTTP_SNAPSHOT_LOAD_FAILED reason=" + reason);
        }
    }
}
