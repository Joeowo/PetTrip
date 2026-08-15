using System.Collections;
using System.IO;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace PetTrip.Tests
{
    /// <summary>
    /// 会话3：Unity 只经 HTTP 消费真实外部模型产物（Responses WorldSpec + Images 概念图
    /// 构建出的 SceneSnapshot）。场景与服务契约与会话2 相同；区别是 127.0.0.1:8000
    /// 后端换成会话3 付费流水线 run 目录的交付服务，且背景 sprite 来自真实生成的
    /// 512x288 PNG。本测试不区分产物来源，来源真实性由 Python 侧 manifest 哈希校验保证。
    /// </summary>
    public sealed class Session3HttpLoadingTests
    {
        [UnityTest]
        public IEnumerator HttpSnapshotLoadsSceneFromRealModelPipeline()
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
                "HTTP snapshot did not load in time. Is the session3 delivery service running on 127.0.0.1:8000?");
            Assert.AreEqual("0.1", loader.LoadedSnapshot.schema_version, "Unexpected schema version.");

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

            // 真实生成图经规范化后必须是 512x288（会话3 契约的目标画布）
            var background = root.Find("background")?.GetComponent<SpriteRenderer>()?.sprite;
            Assert.IsNotNull(background, "background sprite is missing.");
            Assert.AreEqual(512, background.texture.width, "background width must be 512 after normalization.");
            Assert.AreEqual(288, background.texture.height, "background height must be 288 after normalization.");

            var path = Path.GetFullPath(Path.Combine(Application.dataPath, "../TestArtifacts/Session3/unity-screenshot.png"));
            var directory = Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(directory)) Directory.CreateDirectory(directory);
            Session1ScreenshotCapture.Capture(Camera.main, path);
            Assert.IsTrue(File.Exists(path));
            Debug.Log("PETTRIP_SESSION3_HTTP_OK path=" + path);
        }
    }
}
