#if UNITY_EDITOR
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace PetTrip.Editor
{
    public static class Session1ProjectBootstrap
    {
        private const string Root = "Assets/PetTrip";

        public static void Create()
        {
            CreateFolders();
            var renderer = CreateRenderer();
            var pipeline = CreatePipeline(renderer);
            GraphicsSettings.defaultRenderPipeline = pipeline;
            QualitySettings.renderPipeline = pipeline;
            PlayerSettings.defaultScreenWidth = 512;
            PlayerSettings.defaultScreenHeight = 288;
            PlayerSettings.resizableWindow = false;

            var background = CreateSprite("beach_background", 512, 288, DrawBackground);
            var lighthouse = CreateSprite("lighthouse", 80, 160, DrawLighthouse);
            var pet = CreateSprite("pet", 64, 64, DrawPet);
            var shelter = CreateSprite("small_shelter", 96, 72, DrawShelter);
            CreateScene(background, lighthouse, pet, shelter);
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
            Debug.Log("PETTRIP_BOOTSTRAP_OK renderer=URP2D scene=Session1Beach canvas=512x288");
        }

        private static void CreateFolders()
        {
            Directory.CreateDirectory(Path.Combine(Application.dataPath, "PetTrip/Art/Session1"));
            Directory.CreateDirectory(Path.Combine(Application.dataPath, "PetTrip/Scenes"));
            Directory.CreateDirectory(Path.Combine(Application.dataPath, "PetTrip/Settings"));
        }

        private static Renderer2DData CreateRenderer()
        {
            var path = Root + "/Settings/PetTrip2DRenderer.asset";
            var existing = AssetDatabase.LoadAssetAtPath<Renderer2DData>(path);
            if (existing != null) return existing;
            var renderer = ScriptableObject.CreateInstance<Renderer2DData>();
            AssetDatabase.CreateAsset(renderer, path);
            return renderer;
        }

        private static UniversalRenderPipelineAsset CreatePipeline(Renderer2DData renderer)
        {
            var path = Root + "/Settings/PetTripURP.asset";
            var existing = AssetDatabase.LoadAssetAtPath<UniversalRenderPipelineAsset>(path);
            if (existing != null) return existing;
            var pipeline = UniversalRenderPipelineAsset.Create(renderer);
            AssetDatabase.CreateAsset(pipeline, path);
            return pipeline;
        }

        private static Sprite CreateSprite(string name, int width, int height, System.Action<Texture2D> draw)
        {
            var path = Root + "/Art/Session1/" + name + ".png";
            var absolutePath = Path.Combine(Directory.GetParent(Application.dataPath).FullName, path);
            if (!File.Exists(absolutePath))
            {
                var texture = new Texture2D(width, height, TextureFormat.RGBA32, false);
                Clear(texture, Color.clear);
                draw(texture);
                texture.Apply();
                File.WriteAllBytes(absolutePath, texture.EncodeToPNG());
                Object.DestroyImmediate(texture);
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            }

            var importer = (TextureImporter)AssetImporter.GetAtPath(path);
            importer.textureType = TextureImporterType.Sprite;
            importer.spriteImportMode = SpriteImportMode.Single;
            importer.spritePixelsPerUnit = 16;
            importer.filterMode = FilterMode.Point;
            importer.textureCompression = TextureImporterCompression.Uncompressed;
            importer.mipmapEnabled = false;
            importer.alphaIsTransparency = true;
            importer.SaveAndReimport();
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            var sprite = AssetDatabase.LoadAssetAtPath<Sprite>(path);
            if (sprite == null) throw new InvalidDataException("Sprite import failed: " + path);
            return sprite;
        }

        private static void CreateScene(Sprite background, Sprite lighthouse, Sprite pet, Sprite shelter)
        {
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var cameraObject = new GameObject("Main Camera");
            cameraObject.tag = "MainCamera";
            var camera = cameraObject.AddComponent<Camera>();
            camera.orthographic = true;
            camera.orthographicSize = 9f;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color32(85, 180, 218, 255);
            cameraObject.transform.position = new Vector3(0f, 0f, -10f);

            var root = new GameObject("Session1Runtime");
            var catalog = root.AddComponent<SpriteAssetCatalog>();
            SetSprite(catalog, "beachBackground", background);
            SetSprite(catalog, "lighthouse", lighthouse);
            SetSprite(catalog, "pet", pet);
            SetSprite(catalog, "smallShelter", shelter);
            var builder = root.AddComponent<SnapshotSceneBuilder>();
            SetObject(builder, "assetCatalog", catalog);
            var loader = root.AddComponent<SceneSnapshotLoader>();
            SetObject(loader, "sceneBuilder", builder);

            var scenePath = Root + "/Scenes/Session1Beach.unity";
            EditorSceneManager.SaveScene(scene, scenePath);
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(scenePath, true) };
        }

        private static void SetSprite(Object target, string field, Sprite sprite)
        {
            SetObject(target, field, sprite);
        }

        private static void SetObject(Object target, string field, Object value)
        {
            var serialized = new SerializedObject(target);
            serialized.FindProperty(field).objectReferenceValue = value;
            serialized.ApplyModifiedPropertiesWithoutUndo();
        }

        private static void Clear(Texture2D texture, Color color)
        {
            var pixels = new Color[texture.width * texture.height];
            for (var i = 0; i < pixels.Length; i++) pixels[i] = color;
            texture.SetPixels(pixels);
        }

        private static void FillRect(Texture2D texture, int x, int y, int width, int height, Color color)
        {
            for (var py = Mathf.Max(0, y); py < Mathf.Min(texture.height, y + height); py++)
                for (var px = Mathf.Max(0, x); px < Mathf.Min(texture.width, x + width); px++)
                    texture.SetPixel(px, py, color);
        }

        private static void DrawBackground(Texture2D texture)
        {
            FillRect(texture, 0, 0, 512, 92, new Color32(229, 199, 133, 255));
            FillRect(texture, 0, 92, 512, 74, new Color32(54, 151, 184, 255));
            FillRect(texture, 0, 166, 512, 122, new Color32(112, 205, 230, 255));
            FillRect(texture, 54, 232, 110, 12, new Color32(244, 247, 238, 220));
            FillRect(texture, 318, 248, 132, 10, new Color32(244, 247, 238, 220));
        }

        private static void DrawLighthouse(Texture2D texture)
        {
            FillRect(texture, 20, 0, 40, 112, new Color32(247, 239, 216, 255));
            FillRect(texture, 20, 28, 40, 20, new Color32(199, 66, 62, 255));
            FillRect(texture, 20, 78, 40, 18, new Color32(199, 66, 62, 255));
            FillRect(texture, 12, 108, 56, 20, new Color32(48, 67, 76, 255));
            FillRect(texture, 20, 128, 40, 20, new Color32(244, 201, 82, 255));
            FillRect(texture, 8, 148, 64, 8, new Color32(199, 66, 62, 255));
        }

        private static void DrawPet(Texture2D texture)
        {
            FillRect(texture, 16, 8, 32, 34, new Color32(84, 74, 67, 255));
            FillRect(texture, 12, 34, 40, 22, new Color32(104, 91, 80, 255));
            FillRect(texture, 16, 52, 10, 10, new Color32(104, 91, 80, 255));
            FillRect(texture, 38, 52, 10, 10, new Color32(104, 91, 80, 255));
            FillRect(texture, 25, 43, 4, 4, Color.white);
            FillRect(texture, 36, 43, 4, 4, Color.white);
        }

        private static void DrawShelter(Texture2D texture)
        {
            FillRect(texture, 12, 8, 72, 44, new Color32(229, 131, 73, 255));
            FillRect(texture, 2, 48, 92, 14, new Color32(49, 78, 82, 255));
            FillRect(texture, 34, 8, 28, 30, new Color32(57, 47, 43, 255));
        }
    }
}
#endif
