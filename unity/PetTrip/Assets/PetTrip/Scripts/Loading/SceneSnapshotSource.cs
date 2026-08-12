using System.IO;
using UnityEngine;

namespace PetTrip
{
    public static class SceneSnapshotSource
    {
        public const string RelativePath = "PetTrip/scene-session-1.json";

        public static string ReadJson()
        {
            var path = Path.Combine(Application.streamingAssetsPath, RelativePath);
            if (!File.Exists(path))
            {
                throw new FileNotFoundException("SceneSnapshot fixture was not found.", path);
            }

            return File.ReadAllText(path);
        }
    }
}
