using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;

namespace PetTrip
{
    /// <summary>
    /// 会话2：经 HTTP 下载 PNG 并在运行时创建 Sprite 的素材解析器。
    /// 不持有预绑定资产；Resolve 返回由 SceneSnapshot 驱动下载的 Sprite。
    /// </summary>
    public sealed class HttpSpriteProvider : SpriteProvider
    {
        [SerializeField] private float pixelsPerUnit = 16f;

        private readonly Dictionary<string, Sprite> sprites = new();

        public string LastError { get; private set; }

        public IReadOnlyCollection<string> LoadedAssetIds => sprites.Keys;

        public override Sprite Resolve(string assetId) =>
            sprites.TryGetValue(assetId, out var sprite) ? sprite : null;

        public IEnumerator LoadAll(string baseUrl, IEnumerable<string> assetIds)
        {
            LastError = null;
            sprites.Clear();
            foreach (var assetId in assetIds)
            {
                using var request = UnityWebRequestTexture.GetTexture(baseUrl + "/assets/" + assetId + ".png");
                request.timeout = 10;
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                {
                    LastError = "HTTP sprite download failed: " + assetId + " -> " + request.error;
                    yield break;
                }
                var texture = ((DownloadHandlerTexture)request.downloadHandler).texture;
                texture.filterMode = FilterMode.Point;
                sprites[assetId] = Sprite.Create(
                    texture,
                    new Rect(0, 0, texture.width, texture.height),
                    new Vector2(0.5f, 0.5f),
                    pixelsPerUnit);
            }
        }
    }
}
