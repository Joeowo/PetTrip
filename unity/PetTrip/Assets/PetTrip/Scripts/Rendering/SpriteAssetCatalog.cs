using UnityEngine;

namespace PetTrip
{
    public sealed class SpriteAssetCatalog : MonoBehaviour
    {
        [SerializeField] private Sprite beachBackground;
        [SerializeField] private Sprite lighthouse;
        [SerializeField] private Sprite pet;
        [SerializeField] private Sprite smallShelter;

        public Sprite Resolve(string assetId)
        {
            switch (assetId)
            {
                case "beach_background": return beachBackground;
                case "lighthouse": return lighthouse;
                case "pet": return pet;
                case "small_shelter": return smallShelter;
                default: return null;
            }
        }
    }
}
