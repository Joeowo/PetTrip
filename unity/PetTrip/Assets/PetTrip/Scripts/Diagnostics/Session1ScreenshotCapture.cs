using System.IO;
using UnityEngine;

namespace PetTrip
{
    public static class Session1ScreenshotCapture
    {
        public static string Capture(Camera camera, string outputPath)
        {
            var directory = Path.GetDirectoryName(outputPath);
            if (!string.IsNullOrEmpty(directory)) Directory.CreateDirectory(directory);
            var target = new RenderTexture(512, 288, 24, RenderTextureFormat.ARGB32);
            var texture = new Texture2D(512, 288, TextureFormat.RGBA32, false);
            var previous = RenderTexture.active;
            camera.targetTexture = target;
            camera.Render();
            RenderTexture.active = target;
            texture.ReadPixels(new Rect(0, 0, 512, 288), 0, 0);
            texture.Apply();
            File.WriteAllBytes(outputPath, texture.EncodeToPNG());
            camera.targetTexture = null;
            RenderTexture.active = previous;
            Object.DestroyImmediate(target);
            Object.DestroyImmediate(texture);
            return outputPath;
        }
    }
}
