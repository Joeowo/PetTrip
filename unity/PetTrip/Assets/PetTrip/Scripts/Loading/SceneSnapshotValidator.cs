using System;
using System.Collections.Generic;
using UnityEngine;

namespace PetTrip
{
    public static class SceneSnapshotValidator
    {
        private static readonly HashSet<string> AllowedAssets = new HashSet<string>
        {
            "beach_background", "lighthouse", "pet"
        };

        public static void Validate(SceneSnapshot snapshot)
        {
            if (snapshot == null) throw new ArgumentException("Snapshot is null.");
            var version = snapshot.schema_version;
            if (version != "0.1" && version != "0.2") throw new ArgumentException("Unsupported schema_version.");
            if (snapshot.scene_id != "session1_beach") throw new ArgumentException("Unsupported scene_id.");
            if (snapshot.canvas == null || snapshot.canvas.width != 512 || snapshot.canvas.height != 288 || snapshot.canvas.pixels_per_unit != 16)
                throw new ArgumentException("Canvas must be 512x288 at 16 pixels per unit.");
            if (snapshot.layers == null || snapshot.layers.Length != 3) throw new ArgumentException("Exactly three layers are required.");

            var ids = new HashSet<string>();
            var required = new HashSet<string> { "background", "lighthouse", "pet" };
            foreach (var layer in snapshot.layers)
            {
                if (layer == null || string.IsNullOrEmpty(layer.id) || !ids.Add(layer.id)) throw new ArgumentException("Layer IDs must be unique.");
                if (!required.Remove(layer.id)) throw new ArgumentException("Unexpected or duplicate layer ID: " + layer.id);
                if (!AllowedAssets.Contains(layer.asset_id)) throw new ArgumentException("Unknown asset: " + layer.asset_id);
                ValidatePoint(layer.position, "layer " + layer.id, snapshot.canvas);
            }
            if (required.Count != 0) throw new ArgumentException("Required layer is missing.");

            if (snapshot.activity_zone == null || snapshot.activity_zone.type != "polygon" || snapshot.activity_zone.points == null || snapshot.activity_zone.points.Length != 4)
                throw new ArgumentException("A four-point activity polygon is required.");
            foreach (var point in snapshot.activity_zone.points) ValidatePoint(point, "activity zone", snapshot.canvas);

            if (snapshot.interactions == null || snapshot.interactions.Length != 1) throw new ArgumentException("Exactly one interaction is required.");
            var interaction = snapshot.interactions[0];
            if (interaction == null || interaction.id != "pet_wave" || interaction.kind != "pet_action" || interaction.radius <= 0)
                throw new ArgumentException("The interaction must be pet_wave.");
            ValidatePoint(interaction.anchor, "pet_wave", snapshot.canvas);

            if (snapshot.build_slots == null || snapshot.build_slots.Length != 1) throw new ArgumentException("Exactly one build slot is required.");
            var slot = snapshot.build_slots[0];
            if (slot == null || slot.id != "small_shelter" || slot.allowed_prefabs == null || slot.allowed_prefabs.Length != 1 || slot.allowed_prefabs[0] != "small_shelter")
                throw new ArgumentException("The build slot must allow only small_shelter.");
            ValidatePoint(slot.position, "small_shelter", snapshot.canvas);
            ValidatePlacement(slot, version);
        }

        /// <summary>v0.1 没有放置状态；v0.2 只允许空（未放置）或 small_shelter。</summary>
        private static void ValidatePlacement(BuildSlotSpec slot, string version)
        {
            var placed = slot.placed_prefab;
            if (version == "0.1")
            {
                if (!string.IsNullOrEmpty(placed))
                    throw new ArgumentException("schema 0.1 must not carry placed_prefab.");
                return;
            }
            if (string.IsNullOrEmpty(placed)) return;
            if (placed != "small_shelter")
                throw new ArgumentException("Unsupported placed_prefab: " + placed);
        }

        private static void ValidatePoint(PixelPoint point, string label, CanvasSpec canvas)
        {
            if (point == null || point.x < 0 || point.x > canvas.width || point.y < 0 || point.y > canvas.height)
                throw new ArgumentException("Point is outside the canvas: " + label);
        }
    }
}
