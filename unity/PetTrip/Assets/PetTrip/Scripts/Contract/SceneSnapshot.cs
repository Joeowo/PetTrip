using System;
using UnityEngine;

namespace PetTrip
{
    [Serializable]
    public sealed class SceneSnapshot
    {
        public string schema_version;
        public string scene_id;
        public CanvasSpec canvas;
        public LayerSpec[] layers;
        public ActivityZoneSpec activity_zone;
        public InteractionSpec[] interactions;
        public BuildSlotSpec[] build_slots;
    }

    [Serializable]
    public sealed class CanvasSpec
    {
        public int width;
        public int height;
        public int pixels_per_unit;
    }

    [Serializable]
    public sealed class PixelPoint
    {
        public float x;
        public float y;
    }

    [Serializable]
    public sealed class LayerSpec
    {
        public string id;
        public string asset_id;
        public int sorting_order;
        public PixelPoint position;
    }

    [Serializable]
    public sealed class ActivityZoneSpec
    {
        public string id;
        public string type;
        public PixelPoint[] points;
    }

    [Serializable]
    public sealed class InteractionSpec
    {
        public string id;
        public string kind;
        public PixelPoint anchor;
        public float radius;
    }

    [Serializable]
    public sealed class BuildSlotSpec
    {
        public string id;
        public PixelPoint position;
        public string[] allowed_prefabs;
    }
}
