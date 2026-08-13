#if UNITY_EDITOR
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace PetTrip.Editor
{
    /// <summary>
    /// 创建会话2 场景 Session2Beach：HttpSpriteProvider + SnapshotSceneBuilder +
    /// HttpSceneSnapshotLoader。不预绑定 Sprite（素材经 HTTP 下载）。
    /// 通过 executeMethod PetTrip.Editor.Session2ProjectBootstrap.Create 调用。
    /// </summary>
    public static class Session2ProjectBootstrap
    {
        private const string Root = "Assets/PetTrip";

        public static void Create()
        {
            CreateScene();
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
            Debug.Log("PETTRIP_SESSION2_BOOTSTRAP_OK scene=Session2Beach");
        }

        private static void CreateScene()
        {
            Directory.CreateDirectory(Path.Combine(Application.dataPath, "PetTrip/Scenes"));

            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

            var cameraObject = new GameObject("Main Camera");
            cameraObject.tag = "MainCamera";
            var camera = cameraObject.AddComponent<Camera>();
            camera.orthographic = true;
            camera.orthographicSize = 9f;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color32(85, 180, 218, 255);
            cameraObject.transform.position = new Vector3(0f, 0f, -10f);

            var root = new GameObject("Session2Runtime");
            var provider = root.AddComponent<HttpSpriteProvider>();
            var builder = root.AddComponent<SnapshotSceneBuilder>();
            SetObject(builder, "assetCatalog", provider);
            var loader = root.AddComponent<HttpSceneSnapshotLoader>();
            SetObject(loader, "sceneBuilder", builder);
            SetObject(loader, "spriteProvider", provider);
            SetString(loader, "baseUrl", "http://127.0.0.1:8000");

            var scenePath = Root + "/Scenes/Session2Beach.unity";
            EditorSceneManager.SaveScene(scene, scenePath);

            var buildScenes = new List<EditorBuildSettingsScene>(EditorBuildSettings.scenes);
            if (!buildScenes.Exists(s => s.path == scenePath))
                buildScenes.Add(new EditorBuildSettingsScene(scenePath, true));
            EditorBuildSettings.scenes = buildScenes.ToArray();
        }

        private static void SetObject(Object target, string field, Object value)
        {
            var serialized = new SerializedObject(target);
            serialized.FindProperty(field).objectReferenceValue = value;
            serialized.ApplyModifiedPropertiesWithoutUndo();
        }

        private static void SetString(Object target, string field, string value)
        {
            var serialized = new SerializedObject(target);
            serialized.FindProperty(field).stringValue = value;
            serialized.ApplyModifiedPropertiesWithoutUndo();
        }
    }
}
#endif
