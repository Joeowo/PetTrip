using System.Collections;
using System.IO;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace PetTrip.Tests
{
    public sealed class Session2HttpLoadingTests
    {
        [UnityTest]
        public IEnumerator HttpSnapshotLoadsSceneFromContentService()
        {
            SceneManager.LoadScene("Session2Beach");
            yield return null;

            HttpSceneSnapshotLoader loader = null;
            var deadline = Time.realtimeSinceStartup + 15f;
            while (Time.realtimeSinceStartup < deadline)
            {
                loader = Object.FindFirstObjectByType<HttpSceneSnapshotLoader>();
                if (loader != null && (loader.IsLoaded || !string.IsNullOrEmpty(loader.LoadError)))
                    break;
                yield return new WaitForSeconds(0.2f);
            }

            Assert.IsNotNull(loader, "HttpSceneSnapshotLoader not found in Session2Beach scene.");
            Assert.IsNull(loader.LoadError, "HTTP load failed: " + loader.LoadError);
            Assert.IsTrue(
                loader.IsLoaded,
                "HTTP snapshot did not load in time. Is the content service running on 127.0.0.1:8000?");

            var root = loader.GeneratedScene.transform;
            Assert.IsNotNull(root.Find("background"));
            Assert.IsNotNull(root.Find("lighthouse"));
            Assert.IsNotNull(root.Find("pet"));
            Assert.IsNotNull(root.Find("beach_foreground"));
            Assert.IsNotNull(root.Find("pet_wave"));
            Assert.IsNotNull(root.Find("small_shelter"));

            var provider = Object.FindFirstObjectByType<HttpSpriteProvider>();
            Assert.IsNotNull(provider, "HttpSpriteProvider not found.");
            CollectionAssert.AreEquivalent(
                new[] { "beach_background", "lighthouse", "pet", "small_shelter" },
                provider.LoadedAssetIds,
                "Sprites were not loaded over HTTP.");

            var path = Path.GetFullPath(Path.Combine(Application.dataPath, "../TestArtifacts/Session2/unity-screenshot.png"));
            var directory = Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(directory)) Directory.CreateDirectory(directory);
            Session1ScreenshotCapture.Capture(Camera.main, path);
            Assert.IsTrue(File.Exists(path));
            Debug.Log("PETTRIP_SESSION2_HTTP_OK path=" + path);
        }
    }
}
