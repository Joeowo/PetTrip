using UnityEngine;

namespace PetTrip
{
    /// <summary>
    /// 素材解析抽象：把 asset_id 映射为 Sprite。
    /// 会话1 由 <see cref="SpriteAssetCatalog"/> 用预绑定 Sprite 实现；
    /// 会话2 由 <see cref="HttpSpriteProvider"/> 用经 HTTP 下载的 PNG 实现。
    /// 采用抽象基类而非接口，以便 Unity 序列化字段引用具体实现。
    /// </summary>
    public abstract class SpriteProvider : MonoBehaviour
    {
        public abstract Sprite Resolve(string assetId);
    }
}
