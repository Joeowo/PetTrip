using System;
using UnityEngine;

namespace PetTrip
{
    public sealed class SnapshotSceneBuilder : MonoBehaviour
    {
        private const string GeneratedRootName = "GeneratedScene";
        [SerializeField] private SpriteProvider assetCatalog;

        public GameObject Build(SceneSnapshot snapshot)
        {
            if (assetCatalog == null) throw new InvalidOperationException("SpriteAssetCatalog is not assigned.");
            var existing = transform.Find(GeneratedRootName);
            if (existing != null) DestroyImmediate(existing.gameObject);

            var root = new GameObject(GeneratedRootName);
            root.transform.SetParent(transform, false);
            foreach (var layer in snapshot.layers) CreateLayer(root.transform, layer, snapshot);
            CreateActivityZone(root.transform, snapshot.activity_zone, snapshot.canvas);
            CreateInteraction(root.transform, snapshot.interactions[0], snapshot.canvas);
            CreateShelter(root.transform, snapshot.build_slots[0], snapshot);
            return root;
        }

        /// <summary>
        /// 数据层放置：返回升级为 v0.2 且槽位记录 placed_prefab 的新快照；
        /// 未被槽位 allowed_prefabs 允许的 Prefab 一律拒绝。
        /// </summary>
        public static SceneSnapshot PlacePrefab(SceneSnapshot snapshot, string slotId, string prefabId)
        {
            if (snapshot == null) throw new ArgumentNullException(nameof(snapshot));
            if (string.IsNullOrEmpty(prefabId)) throw new ArgumentException("prefabId must not be empty.");
            var slot = Array.Find(snapshot.build_slots, item => item.id == slotId);
            if (slot == null) throw new ArgumentException("Unknown build slot: " + slotId);
            if (Array.IndexOf(slot.allowed_prefabs, prefabId) < 0)
                throw new ArgumentException("Prefab is not allowed by the slot: " + prefabId);

            var placed = CloneSnapshot(snapshot);
            placed.schema_version = "0.2";
            var target = Array.Find(placed.build_slots, item => item.id == slotId);
            target.placed_prefab = prefabId;
            SceneSnapshotValidator.Validate(placed);
            return placed;
        }

        /// <summary>把快照序列化为服务端契约 JSON（v0.2 上传用）。</summary>
        public static string SerializeSnapshot(SceneSnapshot snapshot)
        {
            return JsonUtility.ToJson(snapshot);
        }

        private static SceneSnapshot CloneSnapshot(SceneSnapshot source)
        {
            var clone = new SceneSnapshot
            {
                schema_version = source.schema_version,
                scene_id = source.scene_id,
                canvas = new CanvasSpec
                {
                    width = source.canvas.width,
                    height = source.canvas.height,
                    pixels_per_unit = source.canvas.pixels_per_unit,
                },
                layers = CloneArray(source.layers, layer => new LayerSpec
                {
                    id = layer.id,
                    asset_id = layer.asset_id,
                    sorting_order = layer.sorting_order,
                    position = ClonePoint(layer.position),
                }),
                activity_zone = new ActivityZoneSpec
                {
                    id = source.activity_zone.id,
                    type = source.activity_zone.type,
                    points = CloneArray(source.activity_zone.points, ClonePoint),
                },
                interactions = CloneArray(source.interactions, interaction => new InteractionSpec
                {
                    id = interaction.id,
                    kind = interaction.kind,
                    anchor = ClonePoint(interaction.anchor),
                    radius = interaction.radius,
                }),
                build_slots = CloneArray(source.build_slots, slot => new BuildSlotSpec
                {
                    id = slot.id,
                    position = ClonePoint(slot.position),
                    allowed_prefabs = (string[])slot.allowed_prefabs.Clone(),
                    placed_prefab = slot.placed_prefab,
                }),
            };
            return clone;
        }

        private static T[] CloneArray<T>(T[] source, Func<T, T> cloneItem)
        {
            var clone = new T[source.Length];
            for (var i = 0; i < source.Length; i++) clone[i] = cloneItem(source[i]);
            return clone;
        }

        private static PixelPoint ClonePoint(PixelPoint point)
        {
            return new PixelPoint { x = point.x, y = point.y };
        }

        private void CreateLayer(Transform root, LayerSpec layer, SceneSnapshot snapshot)
        {
            var sprite = assetCatalog.Resolve(layer.asset_id);
            if (sprite == null) throw new InvalidOperationException("Sprite is not assigned: " + layer.asset_id);
            var item = new GameObject(layer.id);
            item.transform.SetParent(root, false);
            item.transform.localPosition = ToWorld(layer.position, snapshot.canvas);
            var renderer = item.AddComponent<SpriteRenderer>();
            renderer.sprite = sprite;
            renderer.sortingOrder = layer.sorting_order;
            if (layer.id == "pet")
            {
                var movement = item.AddComponent<PetMovement>();
                movement.Configure(snapshot.activity_zone, snapshot.canvas);
            }
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

        private void CreateShelter(Transform root, BuildSlotSpec slot, SceneSnapshot snapshot)
        {
            // v0.2 起渲染由 placed_prefab 字段驱动；v0.1 无该字段，维持历史行为
            // 渲染 allowed_prefabs[0]（会话1-3 的槽位即小窝）。
            string prefabId;
            if (snapshot.schema_version == "0.2")
            {
                if (string.IsNullOrEmpty(slot.placed_prefab)) return;
                prefabId = slot.placed_prefab;
            }
            else
            {
                prefabId = slot.allowed_prefabs[0];
            }

            var item = new GameObject(slot.id);
            item.transform.SetParent(root, false);
            item.transform.localPosition = ToWorld(slot.position, snapshot.canvas);
            var renderer = item.AddComponent<SpriteRenderer>();
            renderer.sprite = assetCatalog.Resolve(prefabId);
            if (renderer.sprite == null) throw new InvalidOperationException("Sprite is not assigned: " + prefabId);
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
