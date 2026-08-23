using System;
using NUnit.Framework;
using UnityEngine;

namespace PetTrip.Tests
{
    /// <summary>
    /// 会话4：v0.2 契约（placed_prefab）、数据层放置与活动区点内算法的编辑器测试。
    /// </summary>
    public sealed class Session4ContractTests
    {
        private static SceneSnapshot Fixture()
        {
            return JsonUtility.FromJson<SceneSnapshot>(SceneSnapshotSource.ReadJson());
        }

        [Test]
        public void LegacyFixtureStaysValidUnderCurrentValidator()
        {
            var snapshot = Fixture();
            Assert.AreEqual("0.1", snapshot.schema_version);
            Assert.DoesNotThrow(() => SceneSnapshotValidator.Validate(snapshot));
        }

        [Test]
        public void V02WithoutPlacementIsValid()
        {
            var snapshot = Fixture();
            snapshot.schema_version = "0.2";
            Assert.IsTrue(string.IsNullOrEmpty(snapshot.build_slots[0].placed_prefab));
            Assert.DoesNotThrow(() => SceneSnapshotValidator.Validate(snapshot));
        }

        [Test]
        public void V02WithShelterPlacementIsValid()
        {
            var snapshot = Fixture();
            snapshot.schema_version = "0.2";
            snapshot.build_slots[0].placed_prefab = "small_shelter";
            Assert.DoesNotThrow(() => SceneSnapshotValidator.Validate(snapshot));
        }

        [Test]
        public void V02WithUnknownPlacementFails()
        {
            var snapshot = Fixture();
            snapshot.schema_version = "0.2";
            snapshot.build_slots[0].placed_prefab = "rocket";
            Assert.Throws<ArgumentException>(() => SceneSnapshotValidator.Validate(snapshot));
        }

        [Test]
        public void V01WithPlacementFails()
        {
            var snapshot = Fixture();
            snapshot.build_slots[0].placed_prefab = "small_shelter";
            Assert.Throws<ArgumentException>(() => SceneSnapshotValidator.Validate(snapshot));
        }

        [Test]
        public void PlacePrefabUpgradesToV02AndKeepsBusinessFields()
        {
            var source = Fixture();
            var placed = SnapshotSceneBuilder.PlacePrefab(source, "small_shelter", "small_shelter");
            Assert.AreEqual("0.2", placed.schema_version);
            Assert.AreEqual("small_shelter", placed.build_slots[0].placed_prefab);
            // 原快照不被修改（放置是纯函数）
            Assert.AreEqual("0.1", source.schema_version);
            Assert.IsTrue(string.IsNullOrEmpty(source.build_slots[0].placed_prefab));
            // 除版本与放置外业务字段一致
            Assert.AreEqual(source.layers.Length, placed.layers.Length);
            Assert.AreEqual(source.layers[2].position.x, placed.layers[2].position.x);
            Assert.AreEqual(source.build_slots[0].position.y, placed.build_slots[0].position.y);
        }

        [Test]
        public void PlacePrefabRejectsUnallowedPrefab()
        {
            var snapshot = Fixture();
            Assert.Throws<ArgumentException>(
                () => SnapshotSceneBuilder.PlacePrefab(snapshot, "small_shelter", "rocket"));
        }

        [Test]
        public void PlacePrefabRejectsUnknownSlot()
        {
            var snapshot = Fixture();
            Assert.Throws<ArgumentException>(
                () => SnapshotSceneBuilder.PlacePrefab(snapshot, "big_castle", "small_shelter"));
        }

        [Test]
        public void SerializedV02IsAcceptedByValidator()
        {
            var snapshot = Fixture();
            var placed = SnapshotSceneBuilder.PlacePrefab(snapshot, "small_shelter", "small_shelter");
            var json = SnapshotSceneBuilder.SerializeSnapshot(placed);
            var roundTrip = JsonUtility.FromJson<SceneSnapshot>(json);
            Assert.DoesNotThrow(() => SceneSnapshotValidator.Validate(roundTrip));
            Assert.AreEqual("small_shelter", roundTrip.build_slots[0].placed_prefab);
        }

        [Test]
        public void PointInPolygonMatchesActivityZone()
        {
            var zone = new[]
            {
                new PixelPoint { x = 48, y = 48 },
                new PixelPoint { x = 464, y = 48 },
                new PixelPoint { x = 464, y = 160 },
                new PixelPoint { x = 48, y = 160 },
            };
            Assert.IsTrue(PetMovement.PointInPolygon(new PixelPoint { x = 100, y = 100 }, zone));
            Assert.IsTrue(PetMovement.PointInPolygon(new PixelPoint { x = 48, y = 48 }, zone)); // 顶点按边界穿越计数
            Assert.IsFalse(PetMovement.PointInPolygon(new PixelPoint { x = 480, y = 200 }, zone));
            Assert.IsFalse(PetMovement.PointInPolygon(new PixelPoint { x = 464.5f, y = 100 }, zone));
        }
    }
}
