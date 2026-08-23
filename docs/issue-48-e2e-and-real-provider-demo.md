# Issue #48 E2E 与真实 Provider 演示

本页说明 Issue #48 的 fixture E2E 与真实 Provider HTTP 演示边界。Fixture
测试验证服务闭环和不可变产物契约；真实演示只记录一次人工可核验的 Provider
结果，不把它作为自动质量门槛。

## Fixture E2E

运行以下命令：

```text
python -m pytest agent_service/tests/test_issue48_fixture_e2e.py -q
```

测试通过同一个 FastAPI HTTP 入口执行 Session、两次 accepted wish、显式
close、Destination dispatch、Manifest 轮询和 SceneArtifact 文件下载。它使用
仓库内的 fixture 工作流，因此不需要外部 Provider 凭证。测试验证：

- 两个 ScenePlan 的 `order_index` 顺序；
- 两个 Artifact 复用同一个环境母图 SHA-256；
- PNG MIME、画布尺寸、render SHA-256 和下载内容一致；
- 重复 dispatch 不新增或重写不可变 Artifact；
- 完整成功闭环的 `publish_eligible` 为 `true`。

## 真实 Provider 演示

在 `agent_service/.env.local` 配置真实服务的 `PILOT_API_KEY`、
`CHAT_BASE_URL`、`CHAT_API_KEY` 和 `CHAT_MODEL`。图片配置默认复用 Chat 配置；如需
单独配置图片服务，设置 `IMAGES_BASE_URL`、`IMAGES_API_KEY` 和 `IMAGES_MODEL`。
普通 `python -m agent_service.run_server` 启动会从该文件加载配置，并使用真实
Chat/Image Provider；fixture 测试通过显式注入 `Settings` 保持 mock 工作流。

启动真实服务后，在另一个终端运行：

```text
set PETTRIP_BASE_URL=http://127.0.0.1:8001
set PETTRIP_API_KEY=<与 PILOT_API_KEY 相同的值>
python agent_service/scripts/run_real_provider_demo.py --output-dir outputs/issue48-real-demo
```

脚本要求 `PETTRIP_BASE_URL` 和 `PETTRIP_API_KEY`。任一变量缺失时退出并报错，
不会用 fixture 或伪造结果替代真实调用。脚本只调用 HTTP API，不直接导入或调用
Coordinator、Chat Provider 或 Image Provider。

脚本会允许操作者逐轮输入愿望；输入空行后发送显式 close。它轮询
`/api/v1/destinations/{destination_id}`，把 Manifest 保存为 `manifest.json`，
逐个读取 SceneArtifact 并下载 PNG，同时重新计算 SHA-256。任何 HTTP 错误或哈希
不一致都会以真实错误退出。

## 边界与已知不稳定性

- Fixture E2E 证明服务编排、持久化和下载契约，不证明外部模型的图像质量。
- 真实演示证明一次真实 Chat + Image Provider 双场景生成；人工可观察两个场景
  是否复用同一环境，但该观察不自动阻断发布。
- 真实 Provider 可能因网络、凭证、模型可用性、超时或内容策略失败。脚本保留
  原始 HTTP 错误，不把失败转换为成功。
- 性能、压力、成本、限流、生产稳定性、T9 fallback、T10 完整错误恢复矩阵和
  T11 可观测性契约不属于本 Issue 的验收范围。
