using System;
using UnityEngine;

namespace PetTrip
{
    /// <summary>
    /// 会话4：宠物移动由 Snapshot 的 activity_zone 字段约束。
    /// 目标像素点在活动多边形内才移动；越界目标被拒绝且位置不变。
    /// </summary>
    public sealed class PetMovement : MonoBehaviour
    {
        private PixelPoint[] zonePoints;
        private CanvasSpec canvas;
        private Vector3 homePosition;

        public bool LastMoveWasRejected { get; private set; }

        public void Configure(ActivityZoneSpec zone, CanvasSpec canvasSpec)
        {
            zonePoints = (PixelPoint[])zone.points.Clone();
            canvas = canvasSpec;
            homePosition = transform.localPosition;
        }

        public bool TryMoveTo(PixelPoint target)
        {
            if (zonePoints == null || canvas == null)
                throw new InvalidOperationException("PetMovement is not configured with an activity zone.");
            if (!PointInPolygon(target, zonePoints))
            {
                LastMoveWasRejected = true;
                Debug.Log("PETTRIP_PET_MOVE_REJECTED target=" + target.x + "," + target.y);
                return false;
            }
            LastMoveWasRejected = false;
            transform.localPosition = ToWorld(target, canvas);
            Debug.Log("PETTRIP_PET_MOVE_OK target=" + target.x + "," + target.y);
            return true;
        }

        public void ReturnHome()
        {
            transform.localPosition = homePosition;
        }

        /// <summary>射线法点内测试（凸/凹多边形均适用），坐标为画布像素。</summary>
        public static bool PointInPolygon(PixelPoint point, PixelPoint[] polygon)
        {
            var inside = false;
            var count = polygon.Length;
            for (int i = 0, j = count - 1; i < count; j = i, i++)
            {
                var pi = polygon[i];
                var pj = polygon[j];
                var crosses = pi.y > point.y != pj.y > point.y;
                if (!crosses) continue;
                var xAtY = (pj.x - pi.x) * (point.y - pi.y) / (pj.y - pi.y) + pi.x;
                if (point.x < xAtY) inside = !inside;
            }
            return inside;
        }

        private static Vector3 ToWorld(PixelPoint point, CanvasSpec canvasSpec)
        {
            return new Vector3(
                (point.x - canvasSpec.width * 0.5f) / canvasSpec.pixels_per_unit,
                (point.y - canvasSpec.height * 0.5f) / canvasSpec.pixels_per_unit,
                0f);
        }
    }
}
