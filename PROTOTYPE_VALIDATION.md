# 统一契约原型验证文档

**原型文件**: `prototype-unified-contract.html`  
**对应 Issue**: [#9 统一目的地规格、场景产物与 Unity 契约](https://github.com/Joeowo/PetTrip/issues/9)  
**创建日期**: 2026-08-21

## 核心问题

如何在已确认的领域边界基础上，形成一套不改变既有交互原则、可供 `/to-spec` 使用的统一数据模型与契约草案？

## 原型目的

这个可交互的 HTML 原型旨在验证以下关键问题：

### 1. destination_requirements 按类别细化的系统复杂度

**验证方式**: 
- 状态面板展示四类需求（required_constraints、suggested_preferences、creative_freedom、forbidden_content）
- 观察这种分类是否便于理解和追溯
- 检查从澄清轮次到 requirements 的映射是否清晰

**当前结论**: 
- ✅ 四类分类符合协议 3.0 文档的定义
- ✅ 从澄清会话到 requirements 的单向锁定流程清晰
- ⚠️ 需验证：具体愿望如何映射到各类别（需要实际 Agent 运行证据）

### 2. destination_spec、scene_plan、scene_artifact 的统一字段边界

**验证方式**:
- 三个对象在状态面板中独立展示
- destination_spec 包含规格版本（spec_version）和内容版本（content_version）
- scene_plan 只包含计划信息，不包含产物
- scene_artifact 包含最终交付资源和坐标

**当前结论**:
- ✅ destination_spec 锁定后不可变（locked 标志位）
- ✅ scene_plan 与 scene_artifact 明确分离
- ✅ 规格版本（模板变化）与内容版本（共建）追溯分离
- ⚠️ 需确认：scene_state（运行时状态）是否需要独立对象

### 3. 共享环境母版、Clean Plate、最终场景产物的契约表达

**验证方式**:
- shared_environment 包含 hero_frame、clean_plate 和 revision
- hero_frame 需人工审美批准
- clean_plate 从 hero_frame 提取并记录来源
- scene_artifact 引用 revision 确保版本一致性

**当前结论**:
- ✅ Hero Frame → Clean Plate 的依赖关系明确
- ✅ 每个资源都有 file_id、url、sha256 用于 Unity 获取和校验
- ✅ revision 字段统一追溯共享环境版本
- ⚠️ 需确认：定位图（黑圈图）和正式 Mask 是否需要单独字段

### 4. 规格版本、内容版本、共建草稿与正式版本的统一追溯

**验证方式**:
- destination_spec.spec_version: 规格版本（人工模板变化）
- destination_spec.content_version: 内容版本（共建递增）
- shared_environment.revision: 环境版本（与 content_version 对应）
- destination_manifest.revision: Unity 可见的当前版本
- destination_manifest.status: 区分 'staging'（暂存）和 'published'（正式）

**当前结论**:
- ✅ 共建流程通过 revision 2 暂存，全部 ready 后原子切换
- ✅ 场景 5"边缘案例：共建版本原子切换"验证了暂存→发布流程
- ✅ 内容版本与环境版本保持一致
- ⚠️ 需确认：spec_version 何时递增（仅人工模板变化还是也包括其他）

### 5. 失败重试、生成决策记录和规划不可执行时的处理

**验证方式**:
- run 对象包含 attempts 和 max_attempts（最多 3 次）
- FAIL_SCENE_RUN 动作自动触发重试或标记为 failed
- 场景 4"边缘案例：场景失败与重试"验证了重试逻辑
- operation_log 记录所有操作的时间戳和 payload

**当前结论**:
- ✅ 单场景失败不影响其他场景（保持原子性）
- ✅ 最多 3 次 attempt 符合 issue #7 决策
- ✅ 失败场景保留 run_id 和 error，可追溯
- ⚠️ 需确认：非场景步骤（如澄清、锁定 spec）失败的重试策略
- ⚠️ 需确认：规划不可执行（如无法生成合法 requirements）时的显式转向逻辑

### 6. 目的地/场景描述与白日梦文案的可选交付字段

**验证方式**:
- 当前原型中 destination_spec 包含 title、setting、core_experience
- 未包含 description 或 daydream_copy 字段

**当前结论**:
- ⚠️ 需决策：是否在 destination_spec 中添加 description 字段（可选）
- ⚠️ 需决策：白日梦文案是否作为独立对象交付还是嵌入 spec

### 7. 与 Unity 的字段命名、交付时机、正式版本切换和资源获取方式

**验证方式**:
- destination_manifest 是 Unity 轮询的主要对象
- 逐场景交付：每个场景 ready 后立即可获取
- 场景 1"正常流程"展示了逐场景补取
- 资源通过 file_id 和 download_url 获取，不内嵌 Base64

**当前结论**:
- ✅ destination_manifest.scenes 数组展示逐场景状态
- ✅ status 字段区分 'planned'、'queued'、'generating'、'ready'、'failed'
- ✅ Unity 可在场景 1 ready 时立即获取，无需等待场景 2
- ⚠️ 需确认：字段命名约定（snake_case vs camelCase）
- ⚠️ 需确认：video_scene_package 的完整 Schema（当前简化为 artifact）

## 引导场景说明

原型包含 5 个引导场景，每个场景验证特定边缘案例：

### 场景 1: 正常流程
验证从愿望澄清到场景生成的标准流程，确认各对象的创建顺序和依赖关系。

### 场景 2: 第三轮自动封盘
验证三个已接受愿望后澄清自动封盘的逻辑（issue #8 决策）。

### 场景 3: 五次未接受输入封盘
验证累计五次未接受输入后澄清自动封盘的逻辑（issue #8 决策）。

### 场景 4: 场景失败与重试
验证场景生成失败后的自动重试逻辑，最多 3 次 attempt（issue #7 决策）。

### 场景 5: 共建版本原子切换
验证共建的暂存版本（revision 2）和原子发布流程，确认所有新版场景都 ready 才能发布。

## 使用方法

1. **双击打开** `prototype-unified-contract.html`（无需服务器）
2. **查看当前状态面板**，观察各对象的字段和关系
3. **使用自由操作按钮**，手动触发任意动作探索边缘情况
4. **切换引导场景标签**，按顺序执行预设步骤验证特定逻辑
5. **观察日志输出**，查看每个动作的时间戳和错误信息

## 核心逻辑模块

原型中的 `ContractLogic` 模块是纯函数式的状态管理器，可以提取到实际代码：

```javascript
const ContractLogic = {
  createInitialState,  // 创建初始状态
  reducer              // (state, action) => newState
};
```

这个模块：
- ✅ 不依赖 DOM 或浏览器 API
- ✅ 所有状态转换都是纯函数
- ✅ 包含完整的业务规则验证
- ✅ 可以直接移植到 TypeScript/Python 后端

## 待验证问题

通过人工点击和观察，以下问题需要进一步决策：

1. **destination_requirements 的粒度**
   - 当前四类是否足够？是否需要更细的子分类？
   - 从自然语言愿望到分类的映射规则如何定义？

2. **scene_state 对象的必要性**
   - 运行时状态（queued、running）是否需要独立于 run？
   - 还是 destination_manifest.scenes[].status 已足够？

3. **定位图和正式 Mask 的存储**
   - 黑圈定位图是否需要在 scene_artifact 中保留？
   - 正式 Mask 的 bbox 是否需要单独字段？

4. **描述性文案的归属**
   - destination 和 scene 的 description 字段是否必需？
   - 白日梦文案是作为可选字段还是独立交付对象？

5. **非场景步骤的失败处理**
   - 澄清阶段失败、requirements 生成失败的重试策略
   - 规划不可执行时的显式转向（预置目的地）

6. **字段命名约定**
   - 整体采用 snake_case 还是 camelCase？
   - 与协议 3.0 文档对齐

## 下一步行动

基于原型验证结果，建议：

1. ✅ **确认核心对象边界**：destination_spec、scene_plan、scene_artifact 分离清晰，可进入 Schema 设计
2. ✅ **确认版本追溯模型**：spec_version、content_version、revision 的三层追溯合理
3. ⚠️ **细化 destination_requirements**：需要实际 Agent 运行来验证分类粒度
4. ⚠️ **补充失败处理细节**：非场景步骤失败和规划不可执行的处理流程
5. ⚠️ **对齐字段命名**：与 Unity 团队确认命名约定并更新协议 3.0 文档
6. ⚠️ **编写正式 JSON Schema**：基于原型验证的边界，冻结 Schema 0.1 版本

## 原型局限性

此原型是**逻辑验证**，不包括：

- ❌ 实际的图像生成和 CV 检测
- ❌ 文件存储和下载的网络层
- ❌ Unity 客户端的实际渲染和交互
- ❌ 数据库持久化和并发控制
- ❌ 完整的错误码和错误信息体系

这些实现细节应在 `/to-spec` 和 `/to-tickets` 阶段补充。

## 验收标准

此原型可以视为成功，如果：

- ✅ 所有引导场景都能按预期执行（包括预期错误）
- ✅ 非开发人员能够理解状态面板的字段含义
- ✅ 点击操作能够清晰展示状态转换
- ✅ 核心逻辑模块可以提取并移植到后端代码
- ✅ 识别出至少 3 个需要进一步决策的问题

---

**记录者**: Claude (Background Agent)  
**验证方式**: 人工交互测试 + 团队评审  
**下一步**: 基于原型结果更新 issue #9，记录决策并关闭 ticket
