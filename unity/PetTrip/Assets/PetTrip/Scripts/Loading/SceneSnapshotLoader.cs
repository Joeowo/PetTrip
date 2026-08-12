using System;
using UnityEngine;

namespace PetTrip
{
    public sealed class SceneSnapshotLoader : MonoBehaviour
    {
        [SerializeField] private SnapshotSceneBuilder sceneBuilder;
        public SceneSnapshot LoadedSnapshot { get; private set; }
        public GameObject GeneratedScene { get; private set; }

        private void Start()
        {
            Load();
        }

        public void Load()
        {
            try
            {
                if (sceneBuilder == null) throw new InvalidOperationException("SnapshotSceneBuilder is not assigned.");
                var json = SceneSnapshotSource.ReadJson();
                var snapshot = JsonUtility.FromJson<SceneSnapshot>(json);
                SceneSnapshotValidator.Validate(snapshot);
                var generated = sceneBuilder.Build(snapshot);
                LoadedSnapshot = snapshot;
                GeneratedScene = generated;
                Debug.Log($"PETTRIP_SNAPSHOT_LOAD_OK schema={snapshot.schema_version} scene={snapshot.scene_id} canvas={snapshot.canvas.width}x{snapshot.canvas.height} layers={snapshot.layers.Length} interactions={snapshot.interactions.Length} build_slots={snapshot.build_slots.Length}");
            }
            catch (Exception exception)
            {
                Debug.LogError("PETTRIP_SNAPSHOT_LOAD_FAILED stage=load reason=" + exception.Message);
                throw;
            }
        }
    }
}
