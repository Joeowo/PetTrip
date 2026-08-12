using System;
using NUnit.Framework;
using UnityEngine;

namespace PetTrip.Tests
{
    public sealed class SceneSnapshotValidationTests
    {
        [Test]
        public void ValidFixturePasses()
        {
            var snapshot = JsonUtility.FromJson<SceneSnapshot>(SceneSnapshotSource.ReadJson());
            Assert.DoesNotThrow(() => SceneSnapshotValidator.Validate(snapshot));
            Assert.AreEqual("pet_wave", snapshot.interactions[0].id);
            Assert.AreEqual("small_shelter", snapshot.build_slots[0].allowed_prefabs[0]);
        }

        [Test]
        public void WrongVersionFails()
        {
            var snapshot = JsonUtility.FromJson<SceneSnapshot>(SceneSnapshotSource.ReadJson());
            snapshot.schema_version = "9.9";
            Assert.Throws<ArgumentException>(() => SceneSnapshotValidator.Validate(snapshot));
        }

        [Test]
        public void VehicleAssetFails()
        {
            var snapshot = JsonUtility.FromJson<SceneSnapshot>(SceneSnapshotSource.ReadJson());
            snapshot.layers[0].asset_id = "vehicle";
            Assert.Throws<ArgumentException>(() => SceneSnapshotValidator.Validate(snapshot));
        }

        [Test]
        public void OutOfBoundsPointFails()
        {
            var snapshot = JsonUtility.FromJson<SceneSnapshot>(SceneSnapshotSource.ReadJson());
            snapshot.build_slots[0].position.x = 600;
            Assert.Throws<ArgumentException>(() => SceneSnapshotValidator.Validate(snapshot));
        }
    }
}
