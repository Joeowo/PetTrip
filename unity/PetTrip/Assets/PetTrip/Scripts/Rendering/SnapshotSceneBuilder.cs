using System;
using UnityEngine;

namespace PetTrip
{
    public sealed class SnapshotSceneBuilder : MonoBehaviour
    {
        private const string GeneratedRootName = "GeneratedScene";
        [SerializeField] private SpriteAssetCatalog assetCatalog;

        public GameObject Build(SceneSnapshot snapshot)
        {
            if (assetCatalog == null) throw new InvalidOperationException("SpriteAssetCatalog is not assigned.");
            var existing = transform.Find(GeneratedRootName);
            if (existing != null) DestroyImmediate(existing.gameObject);

            var root = new GameObject(GeneratedRootName);
            root.transform.SetParent(transform, false);
            foreach (var layer in snapshot.layers) CreateLayer(root.transform, layer, snapshot.canvas);
            CreateActivityZone(root.transform, snapshot.activity_zone, snapshot.canvas);
            CreateInteraction(root.transform, snapshot.interactions[0], snapshot.canvas);
            CreateShelter(root.transform, snapshot.build_slots[0], snapshot.canvas);
            return root;
        }

        private void CreateLayer(Transform root, LayerSpec layer, CanvasSpec canvas)
        {
            var sprite = assetCatalog.Resolve(layer.asset_id);
            if (sprite == null) throw new InvalidOperationException("Sprite is not assigned: " + layer.asset_id);
            var item = new GameObject(layer.id);
            item.transform.SetParent(root, false);
            item.transform.localPosition = ToWorld(layer.position, canvas);
            var renderer = item.AddComponent<SpriteRenderer>();
            renderer.sprite = sprite;
            renderer.sortingOrder = layer.sorting_order;
        }

        private static void CreateActivityZone(Transform root, ActivityZoneSpec zone, CanvasSpec canvas)
        {
            var item = new GameObject(zone.id);
            item.transform.SetParent(root, false);
            var collider = item.AddComponent<PolygonCollider2D>();
            var points = new Vector2[zone.points.Length];
            for (var i = 0; i < points.Length; i++) points[i] = ToWorld2D(zone.points[i], canvas);
            collider.points = points;
            collider.isTrigger = true;
        }

        private static void CreateInteraction(Transform root, InteractionSpec interaction, CanvasSpec canvas)
        {
            var item = new GameObject(interaction.id);
            item.transform.SetParent(root, false);
            item.transform.localPosition = ToWorld(interaction.anchor, canvas);
            var collider = item.AddComponent<CircleCollider2D>();
            collider.radius = interaction.radius / canvas.pixels_per_unit;
            collider.isTrigger = true;
            item.AddComponent<PetWaveInteraction>();
        }

        private void CreateShelter(Transform root, BuildSlotSpec slot, CanvasSpec canvas)
        {
            var item = new GameObject(slot.id);
            item.transform.SetParent(root, false);
            item.transform.localPosition = ToWorld(slot.position, canvas);
            var renderer = item.AddComponent<SpriteRenderer>();
            renderer.sprite = assetCatalog.Resolve("small_shelter");
            if (renderer.sprite == null) throw new InvalidOperationException("Sprite is not assigned: small_shelter");
            renderer.sortingOrder = 30;
        }

        private static Vector3 ToWorld(PixelPoint point, CanvasSpec canvas)
        {
            var value = ToWorld2D(point, canvas);
            return new Vector3(value.x, value.y, 0f);
        }

        private static Vector2 ToWorld2D(PixelPoint point, CanvasSpec canvas)
        {
            return new Vector2(
                (point.x - canvas.width * 0.5f) / canvas.pixels_per_unit,
                (point.y - canvas.height * 0.5f) / canvas.pixels_per_unit);
        }
    }
}
