"""
双场景共享环境生产路线验证 - 实验执行器

这是一个 PROTOTYPE 脚本，用于验证技术可行性，不是生产代码。
"""

import asyncio
import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Literal
import hashlib
from datetime import datetime

# 注意：这是原型脚本，直接复用 pilot4mvp2 的 image_provider
# 生产环境需要独立的依赖管理
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "pilot4mvp2"))

from agent_service.image_provider import (
    OpenAICompatibleImageProvider,
    ImageGenerationRequest,
)


@dataclass
class ConceptDefinition:
    """目的地概念定义"""
    id: str
    name: str
    env_description: str
    pet_state_1: str
    pet_state_2: str


@dataclass
class ExperimentResult:
    """单次实验结果"""
    concept_id: str
    route_id: str
    scene_a_path: str
    scene_b_path: str
    pure_env_path: str | None
    prompts: dict[str, str]
    run_metadata: dict[str, Any]
    success: bool
    error: str | None


# 四个固定概念
CONCEPTS = [
    ConceptDefinition(
        id="C01",
        name="灯塔海岸",
        env_description="黄昏时分的海岸场景，远处矗立着一座白色灯塔，前景是海蚀岩石和沙滩，海浪轻拍，天空呈现温暖的橙粉色渐变",
        pet_state_1="若叶睦（Q版灰蓝短发少女）坐在前景的海蚀岩石上，双手支撑身体，面向大海，姿态放松",
        pet_state_2="若叶睦（Q版灰蓝短发少女）站在海边沙滩上，弯腰探头观察潮水，姿态好奇",
    ),
    ConceptDefinition(
        id="C02",
        name="森林空地",
        env_description="午后的森林空地，阳光透过树冠洒下斑驳光影，中景有一个古老的树桩，远景是密林，地面铺满柔软的苔藓和野花",
        pet_state_1="若叶睦（Q版灰蓝短发少女）坐在中景的树桩上，双腿悬空轻轻晃动，双手放在膝盖上，表情宁静",
        pet_state_2="若叶睦（Q版灰蓝短发少女）在前景草地上奔跑，身体前倾，双臂自然摆动，表情欢快",
    ),
    ConceptDefinition(
        id="C03",
        name="山谷桥梁",
        env_description="清晨的山谷，木质拱桥横跨溪流，桥两侧有简单的木栏杆，远处是晨雾笼罩的山峰，近处溪水清澈见底",
        pet_state_1="若叶睦（Q版灰蓝短发少女）站在桥头，一只手扶着栏杆，身体微微前倾，眺望远方",
        pet_state_2="若叶睦（Q版灰蓝短发少女）站在桥中央，双手撑在栏杆上，探头向下看溪流，姿态好奇",
    ),
    ConceptDefinition(
        id="C04",
        name="湖畔码头",
        env_description="黄昏的湖泊，木质码头从岸边延伸入水，湖面平静如镜，远处山影倒映，天空呈现紫粉色晚霞",
        pet_state_1="若叶睦（Q版灰蓝短发少女）坐在码头尽头，双腿悬空，双手支撑在身后，仰头看天空",
        pet_state_2="若叶睦（Q版灰蓝短发少女）蹲在岸边浅水处，伸手触碰水面，身体保持平衡，表情专注",
    ),
]

# Neva 画风统一参考
NEVA_STYLE_PROMPT = """
Art style: Hand-painted 2D storybook aesthetic inspired by the game Neva, with pastel and gouache-like color blocks, visible but restrained paper/brush textures, clear silhouettes, cinematic horizontal composition.
Fixed horizontal 16:9 format, fixed camera angle.
Foreground: low ground strip with minimal looping small elements.
Midground: main structures, landmarks, or natural subjects.
Background: sky, distant mountains, mist, and emotional atmosphere.
Use only one main color palette per scene: first two colors set the emotional base, third color for structural layers, fourth for shadows and outlines, fifth as 5-10% accent.
Character and environment share the same time, main light direction, color temperature, shadow intensity, and material logic.
Prohibit: additional people, text, watermarks, logos, UI, second pet, unrelated subjects.
"""

NEVA_CHARACTER_PROMPT = """
Character: Wakaba Mutsumi from MYGO!!!!! in Q-version (chibi) form.
- Gray-blue short hair with signature hairstyle silhouette
- Green-cyan eyes
- Cool-toned uniform/stage outfit colors
- Q-version head-to-body ratio (large head, small body)
- Rounded and simplified facial features and body shape
"""


class ExperimentRunner:
    """实验执行器"""

    def __init__(self, output_dir: Path, image_provider: OpenAICompatibleImageProvider):
        self.output_dir = output_dir
        self.image_provider = image_provider
        self.results: list[ExperimentResult] = []

    async def run_all(self):
        """运行全部实验"""
        print("=" * 80)
        print("双场景共享环境生产路线验证实验")
        print("=" * 80)

        for concept in CONCEPTS:
            print(f"\n{'='*80}")
            print(f"概念: {concept.name} ({concept.id})")
            print(f"{'='*80}")

            # 路线 A: 纯环境母版 + 局部宠物编辑
            await self.run_route_a(concept)

            # 路线 B: 完整场景 A 作为参考生成场景 B
            await self.run_route_b(concept)

            # 路线 C: 程序化 Mask + 独立角色生成
            await self.run_route_c(concept)

            # 路线 D: 双场景同 Prompt 批次生成
            await self.run_route_d(concept)

        # 保存结果汇总
        self._save_summary()
        print(f"\n{'='*80}")
        print("实验完成！")
        print(f"结果保存在: {self.output_dir}")
        print(f"{'='*80}")

    async def run_route_a(self, concept: ConceptDefinition):
        """路线 A: 纯环境母版 + 局部宠物编辑"""
        route_id = "RA"
        print(f"\n[{concept.id}-{route_id}] 路线 A: 纯环境母版 + 局部宠物编辑")

        exp_dir = self.output_dir / f"{concept.id}-{route_id}"
        exp_dir.mkdir(parents=True, exist_ok=True)

        prompts = {}
        metadata = {"route": "A", "steps": []}

        try:
            # 步骤 1: 生成纯环境（无宠物）
            print(f"  步骤 1/3: 生成纯环境图...")
            pure_env_prompt = f"{NEVA_STYLE_PROMPT}\n\nScene: {concept.env_description}\n\nIMPORTANT: No character, no pet, pure environment only."
            prompts["pure_env"] = pure_env_prompt

            pure_env_result = await self.image_provider.generate(
                ImageGenerationRequest(prompt=pure_env_prompt)
            )
            pure_env_path = exp_dir / "pure-env.png"
            pure_env_path.write_bytes(pure_env_result.data)
            metadata["steps"].append({
                "step": 1,
                "action": "generate_pure_env",
                "size": f"{pure_env_result.width}x{pure_env_result.height}",
                "hash": hashlib.sha256(pure_env_result.data).hexdigest()[:16],
            })
            print(f"    ✓ 纯环境图已保存")

            # 步骤 2: 基于纯环境 inpaint 宠物状态1
            print(f"  步骤 2/3: 在纯环境上 inpaint 宠物状态1...")
            # 注意：当前 image_provider 只支持 text-to-image
            # 真实实验需要 image-edit 或 inpainting 接口
            # 这里用 PROTOTYPE 简化：直接生成完整场景 A（模拟 inpaint 结果）
            scene_a_prompt = f"{NEVA_STYLE_PROMPT}\n{NEVA_CHARACTER_PROMPT}\n\nScene: {concept.env_description}\n\nCharacter: {concept.pet_state_1}"
            prompts["scene_a"] = scene_a_prompt

            scene_a_result = await self.image_provider.generate(
                ImageGenerationRequest(prompt=scene_a_prompt)
            )
            scene_a_path = exp_dir / "SceneA.png"
            scene_a_path.write_bytes(scene_a_result.data)
            metadata["steps"].append({
                "step": 2,
                "action": "inpaint_pet_state_1",
                "note": "PROTOTYPE: 使用 text-to-image 模拟 inpainting",
                "hash": hashlib.sha256(scene_a_result.data).hexdigest()[:16],
            })
            print(f"    ✓ 场景 A 已生成")

            # 步骤 3: 基于纯环境 inpaint 宠物状态2
            print(f"  步骤 3/3: 在纯环境上 inpaint 宠物状态2...")
            scene_b_prompt = f"{NEVA_STYLE_PROMPT}\n{NEVA_CHARACTER_PROMPT}\n\nScene: {concept.env_description}\n\nCharacter: {concept.pet_state_2}"
            prompts["scene_b"] = scene_b_prompt

            scene_b_result = await self.image_provider.generate(
                ImageGenerationRequest(prompt=scene_b_prompt)
            )
            scene_b_path = exp_dir / "SceneB.png"
            scene_b_path.write_bytes(scene_b_result.data)
            metadata["steps"].append({
                "step": 3,
                "action": "inpaint_pet_state_2",
                "note": "PROTOTYPE: 使用 text-to-image 模拟 inpainting",
                "hash": hashlib.sha256(scene_b_result.data).hexdigest()[:16],
            })
            print(f"    ✓ 场景 B 已生成")

            # 保存 prompts 和 metadata
            (exp_dir / "prompts.json").write_text(json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8")
            (exp_dir / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

            result = ExperimentResult(
                concept_id=concept.id,
                route_id=route_id,
                scene_a_path=str(scene_a_path),
                scene_b_path=str(scene_b_path),
                pure_env_path=str(pure_env_path),
                prompts=prompts,
                run_metadata=metadata,
                success=True,
                error=None,
            )
            self.results.append(result)
            print(f"  ✓ 路线 A 完成")

        except Exception as e:
            print(f"  ✗ 路线 A 失败: {e}")
            result = ExperimentResult(
                concept_id=concept.id,
                route_id=route_id,
                scene_a_path="",
                scene_b_path="",
                pure_env_path="",
                prompts=prompts,
                run_metadata=metadata,
                success=False,
                error=str(e),
            )
            self.results.append(result)

    async def run_route_b(self, concept: ConceptDefinition):
        """路线 B: 完整场景 A 作为参考生成场景 B"""
        route_id = "RB"
        print(f"\n[{concept.id}-{route_id}] 路线 B: 完整场景 A 作为参考生成场景 B")

        exp_dir = self.output_dir / f"{concept.id}-{route_id}"
        exp_dir.mkdir(parents=True, exist_ok=True)

        prompts = {}
        metadata = {"route": "B", "steps": []}

        try:
            # 步骤 1: 生成完整场景 A
            print(f"  步骤 1/2: 生成完整场景 A...")
            scene_a_prompt = f"{NEVA_STYLE_PROMPT}\n{NEVA_CHARACTER_PROMPT}\n\nScene: {concept.env_description}\n\nCharacter: {concept.pet_state_1}"
            prompts["scene_a"] = scene_a_prompt

            scene_a_result = await self.image_provider.generate(
                ImageGenerationRequest(prompt=scene_a_prompt)
            )
            scene_a_path = exp_dir / "SceneA.png"
            scene_a_path.write_bytes(scene_a_result.data)
            metadata["steps"].append({
                "step": 1,
                "action": "generate_scene_a",
                "hash": hashlib.sha256(scene_a_result.data).hexdigest()[:16],
            })
            print(f"    ✓ 场景 A 已生成")

            # 步骤 2: 使用场景 A 作为参考生成场景 B
            print(f"  步骤 2/2: 使用场景 A 作为参考生成场景 B...")
            # 注意：当前 image_provider 不支持参考图
            # PROTOTYPE 简化：用强一致性 Prompt 模拟
            scene_b_prompt = f"{NEVA_STYLE_PROMPT}\n{NEVA_CHARACTER_PROMPT}\n\nScene: {concept.env_description}\n\nIMPORTANT: Keep the EXACT same environment (landmarks, lighting, composition, style) as a reference image.\n\nCharacter: {concept.pet_state_2}\n\nOnly change: character position and pose."
            prompts["scene_b"] = scene_b_prompt

            scene_b_result = await self.image_provider.generate(
                ImageGenerationRequest(prompt=scene_b_prompt)
            )
            scene_b_path = exp_dir / "SceneB.png"
            scene_b_path.write_bytes(scene_b_result.data)
            metadata["steps"].append({
                "step": 2,
                "action": "generate_scene_b_with_reference",
                "note": "PROTOTYPE: 使用强一致性 Prompt 模拟参考图约束",
                "hash": hashlib.sha256(scene_b_result.data).hexdigest()[:16],
            })
            print(f"    ✓ 场景 B 已生成")

            (exp_dir / "prompts.json").write_text(json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8")
            (exp_dir / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

            result = ExperimentResult(
                concept_id=concept.id,
                route_id=route_id,
                scene_a_path=str(scene_a_path),
                scene_b_path=str(scene_b_path),
                pure_env_path=None,
                prompts=prompts,
                run_metadata=metadata,
                success=True,
                error=None,
            )
            self.results.append(result)
            print(f"  ✓ 路线 B 完成")

        except Exception as e:
            print(f"  ✗ 路线 B 失败: {e}")
            result = ExperimentResult(
                concept_id=concept.id,
                route_id=route_id,
                scene_a_path="",
                scene_b_path="",
                pure_env_path=None,
                prompts=prompts,
                run_metadata=metadata,
                success=False,
                error=str(e),
            )
            self.results.append(result)

    async def run_route_c(self, concept: ConceptDefinition):
        """路线 C: 程序化 Mask + 独立角色生成"""
        route_id = "RC"
        print(f"\n[{concept.id}-{route_id}] 路线 C: 程序化 Mask + 独立角色生成")
        print(f"  注意: pilot4mvp3 已证明此路线的 Mask 稳定性问题")

        exp_dir = self.output_dir / f"{concept.id}-{route_id}"
        exp_dir.mkdir(parents=True, exist_ok=True)

        # 记录路线 C 预期失败
        prompts = {"note": "此路线在 pilot4mvp3 中已证明 Mask 不稳定，本次不重复实验"}
        metadata = {
            "route": "C",
            "status": "skipped",
            "reason": "pilot4mvp3 证明 image-2 固定尺寸 Mask 不稳定",
            "reference": "pilot4mvp3/mask-stability-validation-spec.md",
        }

        (exp_dir / "prompts.json").write_text(json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8")
        (exp_dir / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

        result = ExperimentResult(
            concept_id=concept.id,
            route_id=route_id,
            scene_a_path="",
            scene_b_path="",
            pure_env_path="",
            prompts=prompts,
            run_metadata=metadata,
            success=False,
            error="已知路线失败（pilot4mvp3）",
        )
        self.results.append(result)
        print(f"  ⊘ 路线 C 跳过（已有负向证据）")

    async def run_route_d(self, concept: ConceptDefinition):
        """路线 D: 双场景同 Prompt 批次生成"""
        route_id = "RD"
        print(f"\n[{concept.id}-{route_id}] 路线 D: 双场景同 Prompt 批次生成")

        exp_dir = self.output_dir / f"{concept.id}-{route_id}"
        exp_dir.mkdir(parents=True, exist_ok=True)

        prompts = {}
        metadata = {"route": "D", "steps": []}

        try:
            # 独立生成场景 A
            print(f"  步骤 1/2: 生成场景 A（语义一致性约束）...")
            scene_a_prompt = f"{NEVA_STYLE_PROMPT}\n{NEVA_CHARACTER_PROMPT}\n\nScene: {concept.env_description}\n\nCharacter: {concept.pet_state_1}\n\nNOTE: This is Scene A of a two-scene set sharing the same environment."
            prompts["scene_a"] = scene_a_prompt

            scene_a_result = await self.image_provider.generate(
                ImageGenerationRequest(prompt=scene_a_prompt)
            )
            scene_a_path = exp_dir / "SceneA.png"
            scene_a_path.write_bytes(scene_a_result.data)
            metadata["steps"].append({
                "step": 1,
                "action": "generate_scene_a",
                "hash": hashlib.sha256(scene_a_result.data).hexdigest()[:16],
            })
            print(f"    ✓ 场景 A 已生成")

            # 独立生成场景 B（强调环境一致性）
            print(f"  步骤 2/2: 生成场景 B（语义一致性约束）...")
            scene_b_prompt = f"{NEVA_STYLE_PROMPT}\n{NEVA_CHARACTER_PROMPT}\n\nScene: {concept.env_description}\n\nCharacter: {concept.pet_state_2}\n\nNOTE: This is Scene B of a two-scene set. Must share EXACTLY the same environment (landmarks, lighting, composition, color palette) as Scene A. Only the character position and pose differ."
            prompts["scene_b"] = scene_b_prompt

            scene_b_result = await self.image_provider.generate(
                ImageGenerationRequest(prompt=scene_b_prompt)
            )
            scene_b_path = exp_dir / "SceneB.png"
            scene_b_path.write_bytes(scene_b_result.data)
            metadata["steps"].append({
                "step": 2,
                "action": "generate_scene_b",
                "hash": hashlib.sha256(scene_b_result.data).hexdigest()[:16],
            })
            print(f"    ✓ 场景 B 已生成")

            (exp_dir / "prompts.json").write_text(json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8")
            (exp_dir / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

            result = ExperimentResult(
                concept_id=concept.id,
                route_id=route_id,
                scene_a_path=str(scene_a_path),
                scene_b_path=str(scene_b_path),
                pure_env_path=None,
                prompts=prompts,
                run_metadata=metadata,
                success=True,
                error=None,
            )
            self.results.append(result)
            print(f"  ✓ 路线 D 完成")

        except Exception as e:
            print(f"  ✗ 路线 D 失败: {e}")
            result = ExperimentResult(
                concept_id=concept.id,
                route_id=route_id,
                scene_a_path="",
                scene_b_path="",
                pure_env_path=None,
                prompts=prompts,
                run_metadata=metadata,
                success=False,
                error=str(e),
            )
            self.results.append(result)

    def _save_summary(self):
        """保存实验结果汇总"""
        summary = {
            "experiment": "dual-scene-shared-env-validation",
            "timestamp": datetime.now().isoformat(),
            "total_experiments": len(self.results),
            "successful": sum(1 for r in self.results if r.success),
            "failed": sum(1 for r in self.results if not r.success),
            "results": [
                {
                    "concept_id": r.concept_id,
                    "route_id": r.route_id,
                    "success": r.success,
                    "scene_a_path": r.scene_a_path,
                    "scene_b_path": r.scene_b_path,
                    "pure_env_path": r.pure_env_path,
                    "error": r.error,
                }
                for r in self.results
            ],
        }

        summary_path = self.output_dir / "experiment-summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n实验汇总已保存: {summary_path}")


async def main():
    """主入口"""
    # 输出目录
    output_dir = Path(__file__).parent / "outputs"
    output_dir.mkdir(exist_ok=True)

    # 初始化 image provider（需要从环境变量或配置文件读取）
    # PROTOTYPE: 这里硬编码，生产环境需要改为配置管理
    provider = OpenAICompatibleImageProvider(
        base_url="https://api.openai.com/v1",  # 替换为实际 API 端点
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        model="gpt-image-2",
        timeout_seconds=120.0,
        request_size="1792x1024",  # 横版 16:9
        max_decoded_bytes=20 * 1024 * 1024,
        max_image_pixels=10000 * 10000,
    )

    runner = ExperimentRunner(output_dir, provider)
    await runner.run_all()


if __name__ == "__main__":
    print("=" * 80)
    print("双场景共享环境生产路线验证 - PROTOTYPE")
    print("=" * 80)
    print("\n警告：这是一个原型脚本，用于技术验证，不是生产代码。")
    print("需要配置有效的图片生成 API 端点和密钥。\n")

    asyncio.run(main())
