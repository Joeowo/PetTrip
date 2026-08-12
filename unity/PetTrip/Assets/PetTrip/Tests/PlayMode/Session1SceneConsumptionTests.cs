using System.Collections;
using System.IO;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace PetTrip.Tests
{
    public sealed class Session1SceneConsumptionTests
    {
        [UnityTest]
        public IEnumerator SnapshotBuildsSceneAndCapturesEvidence()
        {
            SceneManager.LoadScene("Session1Beach");
            yield return null;
            yield return null;

            var loader = Object.FindFirstObjectByType<SceneSnapshotLoader>();
            Assert.IsNotNull(loader);
            Assert.IsNotNull(loader.GeneratedScene);
            var root = loader.GeneratedScene.transform;
            Assert.IsNotNull(root.Find("background"));
            Assert.IsNotNull(root.Find("lighthouse"));
            Assert.IsNotNull(root.Find("pet"));
            Assert.IsNotNull(root.Find("beach_foreground"));
            Assert.IsNotNull(root.Find("pet_wave"));
            Assert.IsNotNull(root.Find("small_shelter"));
            Assert.Greater(root.Find("small_shelter").position.x, 0f);
            Assert.IsNull(root.Find("vehicle"));

            var wave = root.Find("pet_wave").GetComponent<PetWaveInteraction>();
            wave.Trigger();
            Assert.IsTrue(wave.WasTriggered);

            var path = Path.GetFullPath(Path.Combine(Application.dataPath, "../TestArtifacts/Session1/unity-screenshot.png"));
            Session1ScreenshotCapture.Capture(Camera.main, path);
            Assert.IsTrue(File.Exists(path));
            var bytes = File.ReadAllBytes(path);
            Assert.Greater(bytes.Length, 1024);
            var texture = new Texture2D(2, 2);
            Assert.IsTrue(texture.LoadImage(bytes));
            Assert.AreEqual(512, texture.width);
            Assert.AreEqual(288, texture.height);
            Object.DestroyImmediate(texture);
            Debug.Log("PETTRIP_SCREENSHOT_OK path=" + path);
        }
    }
}
