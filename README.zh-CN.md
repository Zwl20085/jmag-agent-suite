<!-- mcp-name: io.github.Zwl20085/jmag-agent-suite -->
<div align="center">

<img src="docs/images/hero.svg" width="100%" alt="JMAG Agent Suite: MCP server, physics checks and agent skills that let AI run JMAG-Designer FEA and prove the result">

# JMAG Agent Suite 中文说明

[English](README.md) | 简体中文

**面向 JMAG-Designer 的一体化智能体工具包：在 AI 智能体中完成电磁场设计的建模、求解、验证、扫描和报告，并证明结果符合物理规律，而不是碰巧如此。**

[![tests](https://github.com/Zwl20085/jmag-agent-suite/actions/workflows/tests.yml/badge.svg)](https://github.com/Zwl20085/jmag-agent-suite/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)
![JMAG](https://img.shields.io/badge/JMAG--Designer-23.x-0b6e99)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6)
![Tools](https://img.shields.io/badge/MCP%20tools-75-6f42c1)
![Skills](https://img.shields.io/badge/Claude%20skills-10-ff7b00)
![License](https://img.shields.io/badge/license-MIT-green)

</div>

---

## 可以这样提问

> *这是我的电机几何脚本。建立一个 Transient2D 工况点：1500 rpm，峰值电流 10 A，叠厚 50 mm，找出最大转矩对应的电流角，并证明转矩是对的。*

> *对这个 12 槽 10 极设计计算齿槽转矩和空载反电动势，把反电动势常数与手算估值对比，并画出 FFT。*

> *把磁钢厚度扫描 4 个算例，全部求解，然后生成报告，给出每个算例的转矩、损耗和各项不变量。*

> *基于已求解的瞬态研究创建铁耗研究，读取各部件的损耗，并用功率平衡检查。*

> *运行因单元体积为负而中止，转矩是 -0.3 Nm。问题出在哪里？*

随后智能体会使用下面的 MCP 工具和技能族。每个工具都返回
`{"ok", "result", "error": {"message", "hint"}, "jmag_messages"}`，每个设置的值都会回读，智能体也被要求在声称成功之前，用检查来支撑每一个数字。

<table><tr>
<td><img src="docs/images/spm_flux_density.png" width="300" alt="Flux density of a 12-slot 4-pole SPM motor"></td>
<td><img src="docs/images/ccore_flux_density.png" width="300" alt="C-core flux density"></td>
<td><img src="docs/images/circuit_current_source.png" width="300" alt="Circuit built by jmag_circuit_quick"></td>
</tr><tr>
<td align="center"><sub>12s/4p SPM，1500 rpm，由智能体建模并求解。转矩 1.774 Nm，由磁链算得 1.777 Nm。</sub></td>
<td align="center"><sub>自检用 C 形铁心在峰值电流下（铁心柱约 0.73 T，与 L·I/(N·A) 的预测一致）。</sub></td>
<td align="center"><sub>由 <code>jmag_circuit_quick</code> 生成的电路，接线已通过 KVL 检查证明。</sub></td>
</tr></table>

## 为什么做这个项目

商用有限元分析（FEA）软件是为鼠标点击设计的，不是为智能体设计的。以 JMAG 为例，它很少抛出错误（调用失败时返回一个无效对象并在日志中记一行），会被模态对话框阻塞，在运动设置下会悄悄改写步长设置，而且它的 Python API 只能在自带的 Python 3.8 上运行。智能体不能直接“调用 API”。它需要一个中间层，把上述每种情况变成明确的、可执行的反馈，同时还需要工程判断力，能区分正常结果和异常结果。这就是本项目：一个带 75 个工具的 [MCP](https://modelcontextprotocol.io) 服务器，一个纯 Python 的工程检查库，以及十个教智能体像电机设计师一样工作的 [Claude 技能](https://docs.claude.com/en/docs/claude-code/skills)。

## 安装

**环境要求：** Windows，自己持有许可证的 JMAG-Designer 23.x，Python 3.10 或更高版本，以及 Claude Code 或任意 MCP 客户端。
不分发 JSOL 的任何内容；文档类工具在运行时为你自己安装的 JMAG 建立索引。

```powershell
git clone https://github.com/Zwl20085/jmag-agent-suite
cd jmag-agent-suite
.\scripts\install.ps1          # venv + package, registers MCP server "jmag", installs the 10 skills
```

重启 Claude Code 并提问：*run the JMAG self-test*（运行 JMAG 自检）。六项已知答案检查会告诉你工具链和许可证是否正常。
后续步骤见 [docs/quickstart.md](docs/quickstart.md)。

**如果没有任何反应：** 运行 `.venv\Scripts\python -m jmag_bridge --doctor`（安装程序已运行过一次）。每项检查结果为 PASS、WARN 或 FAIL，并附一行 `fix:` 提示。服务器启动失败时，其回溯信息会写入状态文件夹（`%LOCALAPPDATA%\jmag-agent-suite`）中的 `server-startup.log`。按症状逐条排查见：
[docs/install-troubleshooting.md](docs/install-troubleshooting.md)。

**更新 / 卸载：** `git pull; .\scripts\install.ps1` 用于更新；`.\scripts\install.ps1 -Uninstall` 会移除 MCP 注册和技能（保留 `.venv`）。

**安装程序选项：** `-JmagHome <folder>`、`-Python <python.exe>`、`-Scope user|project|local`、`-NoMcp`（无需 Claude
Code）、`-NoSkills`、`-Uninstall`。

**其他 MCP 客户端（Cursor、Codex、Claude Desktop、VS Code）：** 配置片段见
[docs/other-clients.md](docs/other-clients.md)；这些客户端不会加载技能，因此应让智能体先调用 `jmag_guide`。

<details>
<summary>作为 Claude Code 插件（marketplace）安装、手动安装、其他 MCP 客户端、环境变量</summary>

插件（需要 [uv](https://docs.astral.sh/uv/)）：

```
/plugin marketplace add Zwl20085/jmag-agent-suite
/plugin install jmag@jmag-agent-suite
```

手动安装：

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[plots]"
claude mcp add jmag --scope user -- "$PWD\.venv\Scripts\python.exe" -m jmag_bridge
```

任何 MCP 客户端都可以用 `<venv python> -m jmag_bridge`（stdio）运行服务器。若要在本仓库之外使用技能，请把 `.claude/skills/*` 复制到
`%USERPROFILE%\.claude\skills\`。JMAG 安装在非常规位置时：
`.\scripts\install.ps1 -JmagHome "E:\JMAG-Designer23.1"`。

| 环境变量 | 用途 |
|---|---|
| `JMAG_HOME` | JMAG 安装文件夹（自动在 `C:`/`D:`/`E:` 的 `\Program Files\JMAG-Designer*` 下查找） |
| `JMAG_PYTHON` | 覆盖 JMAG 自带的 `python3.8\python.exe` |
| `JMAG_BRIDGE_HOME` | 状态、日志、journal、文档索引（默认 `%LOCALAPPDATA%\jmag-agent-suite`；若存在旧的 `jmag-mcp-bridge` 文件夹则继续使用） |

</details>

## 覆盖范围

电机工程师用 JMAG 做的各项工作，以及智能体目前能完成多少。
**已验证（Verified）** = 在 JMAG-Designer 23.0 上端到端运行并经过数值检查。**工具级（Tool）** = 按文档化的 API 实现，但尚未在真实许可证上运行过。**流程（Recipe）** = 技能引导智能体用现有工具完成。
**脚本（Script）** = 可通过 `jmag_exec_python` 配合 API 查询工具实现。

| 步骤 | SPM | IPM / SynRM | 感应电机 | 变压器 / 电感 | 作动器 / WPT |
|---|---|---|---|---|---|
| 几何（DXF/STEP + 标签） | Verified | Recipe | Recipe | Verified（二维 DXF、三维 STEP） | Recipe |
| 绕组布局和 FEM 线圈 | Verified | Verified | Recipe | Verified（二维电感） | Verified |
| 二维静态研究（L、力、磁链） | Verified | Recipe | Recipe | Verified（电感） | Recipe |
| 三维静态研究（STEP、半模型、`Current` 流入面） | Recipe | Recipe | Recipe | Verified（变压器电感矩阵） | Recipe |
| 带电路和运动的瞬态 | Verified | Recipe | Recipe | Verified（二维电感，无运动） | Recipe |
| 三维瞬态、三维绕组 / FEM 线圈、三维场导出 | Script | Script | Script | Script | Script |
| 设置检查（lint）、求解监控、取消 | Verified | Verified | Verified | Verified | Verified |
| 物理不变量（KVL、能量、功率平衡） | Verified | Verified | Verified | Verified | Verified |
| 齿槽转矩、空载反电动势、工况点、Ld/Lq（`jmag_experiment`） | Verified | Recipe | Recipe | Recipe | Recipe |
| 铁耗研究和损耗表 | Verified | Tool | Tool | Tool | Tool |
| 效率图 | Tool | Tool | Script | n/a | n/a |
| JMAG 算例报告、表格导出 | Verified | Verified | Verified | Verified | Verified |
| 设计表、算例扫描 | Verified | Verified | Verified | Verified | Verified |
| 优化表 | Tool | Tool | Tool | Tool | Tool |
| 绘图、对比、报告 | Verified | Verified | Verified | Verified | Verified |
| 热分析 | Script | Script | Script | Script | Script |
| 力 / NVH 导出 | Script | Script | Script | n/a | Script |

“已验证”一列对应 [12 槽 / 4 极 SPM 示例](examples/spm_motor)和自检用 C 形铁心；这张表同时也是路线图。其他器件类型上标为“Verified”的条目，表示该步骤与器件类型无关。三维静态一行对应 2026-10-19 在 23.0 上运行的两个线性变压器（E 形铁心、罐形铁心），电感与独立有限元结果相差在 0.9 % 以内；变压器开路 / 短路试验仍是流程（Recipe），三维静态以外的三维分析尚未运行（[流程](.claude/skills/jmag-simulation/references/static-3d.md)）。

## 信任机制：反馈阶梯

桥接层按七个层级检查，由低成本到高成本。智能体被要求逐级向上检查，并报告停在了哪一级。

| # | 层级 | 方法 | 正常表现 |
|---|---|---|---|
| 1 | **调用** | 消息日志差异、对每个对象做 `IsValid`、回读每个值、最接近名称匹配 | `ok: true`，读回值 == 设置值 |
| 2 | **设置检查** | `jmag_check(stage="pre")`：材料、叠厚、链接、激励源、接地、线圈方向、匝数、步长分辨率、运动速度 | `PASS` |
| 3 | **求解器** | 报告、`Designer.log`、每步 Newton 迭代次数、已知 JMAG 消息分类 | 无错误，只有已知的提示信息 |
| 4 | **物理不变量** | KVL `v = R·i + dψ/dt` · 能量 `W = ½Σiψ` · 功率平衡 `⟨Σ i dψ/dt⟩ = ⟨T⟩ω` | KVL < 1e-3 · 能量 < 5 % · 功率平衡 < 5 % |
| 5 | **工程估算** | `jmag_estimate` 手算（气隙 B、kw、Kt、Ld/Lq、带气隙铁心的 L、步长方案）并给出预期容差 | 有限元结果在容差内，差异有解释 |
| 6 | **可视化** | B 云图、电路图、模型/网格图像、每个 JMAG 窗口和对话框的截图 | 极、磁通路径、接线 |
| 7 | **工具链** | `jmag_self_test`：在本机和本许可证上做已知答案测试 | 6/6 PASS |

### 在 JMAG-Designer 23.0 上的验证

| 模型 | 检查项 | 结果 |
|---|---|---|
| C 形铁心电感，静态 | L 与解析值对比（不计边缘效应 9.42 mH） | 11.00 mH（二维边缘效应 +17 %） |
| | 能量恒等式 | 0.76 % |
| C 形铁心电感，瞬态 + 电路 | ψ/i 与静态 L 之比 | 1.000005 |
| | KVL 残差 | 2.7 × 10⁻⁷ |
| 12s/4p SPM，1500 rpm，10 A | 转矩与 `3/2·p·ψ₁·I` 对比 | 1.774 对 1.777 Nm（0.2 %） |
| | 机电功率平衡 | 0.12 % |
| | 稳态（最后两个周期） | 0.09 % |
| 12s/4p SPM，`jmag_experiment` | 1500 rpm 时的空载反电动势 | 每相 13.0 V rms，相间相差 120°，`E = ωψ` 自洽 |
| | 齿槽转矩 | 峰峰值 6.06 Nm，主导阶次为每转 12 次（= LCM(12, 4)） |
| | 由三次静态求解得到的 Ld / Lq | 0.367 / 0.366 mH（相等，符合 SPM 的预期） |
| | 工况点功率平衡 | 0.9 % |
| 12s/4p SPM，`jmag_loss_study` | 基于瞬态的铁耗研究 | 定子磁滞损耗 1.998 W + 涡流损耗 2.047 W = 总计 4.045 W |
| 12s/4p SPM，`jmag_run_cases` | 设计表，2 个算例（10 A、5 A） | 1.804 / 0.910 Nm，比值 0.504，每个算例的结果通过 `case=` 获取 |

## 按任务分类的工具

<details open>
<summary>75 个 MCP 工具，全部以 <code>jmag_</code> 为前缀</summary>

| 任务 | 工具 |
|---|---|
| 会话 | `status` `connect` `capabilities` `dialogs` `screenshot` `cleanup` `quit` `doctor`（安装诊断） |
| 项目和几何 | `new_project` `open_project` `save_project` `summary` `inventory` `import_dxf` `import_cad` `list_parts` `rename_parts` `suppress_parts` `model_image` |
| 设计辅助（无需 JMAG） | `estimate` `units` `winding_plan` |
| 研究设置 | `create_study` `duplicate_study` `delete_study` `get_properties` `set_properties` `set_step` `set_equations` `get_equations` |
| 材料 | `find_materials` `create_material` `set_material` |
| 激励和条件 | `add_current` `add_fem_coil` `add_fem_conductor` `winding` `add_condition` `list_conditions` `delete_condition` |
| 电路 | `circuit_quick` `circuit_add` `circuit_summary` `circuit_image` |
| 网格 | `mesh` `mesh_size` |
| 运行 | `check` `run` `run_status` `cancel_run` `solver_report` |
| 标准测试 | `experiment`（齿槽转矩、空载反电动势、工况点、静态电感） |
| 损耗和效率 | `loss_study` `efficiency_map` |
| 参数化和优化 | `case_table` `run_cases` `optimization_table` |
| 结果 | `list_results` `get_result` `export_results` `export_table` `case_report` `contour_image` `physics_check` `compare` `plot` `make_report` |
| 知识库 | `docs_search` `docs_page`（你已安装的 JMAG 手册）`api_lookup`（JMAG Python API） |
| 应急通道和元工具 | `exec_python` `self_test` `journal` `star_repo` `guide`（技能内容，供没有技能功能的客户端使用） |

每个工具的 docstring 都会说明其 JMAG 调用在 23.0 上是**已验证（verified）**还是**未验证（unverified）**（按 API
参考实现）。技能 `jmag/references/tool-map.md` 用一行描述每个工具。

</details>

## 技能

一个路由技能加九个专项技能。每个技能都很短，只在需要时才加载其参考资料。

| 技能 | 教给智能体 |
|---|---|
| `jmag` | 从哪里开始：用哪个技能、安全规则、工具地图 |
| `jmag-simulation` | 建模、检查、运行、验证的循环；反馈阶梯；JMAG 已验证的特殊行为和属性名 |
| `jmag-geometry` | 能干净导入的 DXF/STEP + 标签脚本；SPM、IPM、感应电机、SynRM、电感、螺线管的模板 |
| `jmag-machine-design` | 解析尺寸设计和合理性判断、槽极配合表、绕组系数、把估算变成检查 |
| `jmag-experiments` | 齿槽转矩、空载反电动势、Ld/Lq、工况点、转矩-转速、堵转、开路/短路、力-行程、耦合 |
| `jmag-losses-efficiency` | 铁耗/铜耗/磁钢损耗、损耗研究、效率图、热分析怎么处理 |
| `jmag-parametric-optimisation` | 设计表、算例运行、DOE、几何重新生成、优化表、Pareto 报告 |
| `jmag-postprocess` | 结果键名、稳态窗口、FFT 和脉动、绘图、对比、报告检查清单 |
| `jmag-scripting` | 在 Python 3.8 worker 上安全使用 `jmag_exec_python` 的模式、API 查询、把代码片段升级为工具 |
| `jmag-troubleshooting` | 症状到工具再到修复：挂起、对话框、崩溃、不收敛、转矩错误、表格为空 |

在一次针对故障排查提示（“IPM 运行因单元体积为负而中止，转矩 -0.3 Nm”）的 A/B 测试中，没有技能的智能体编造了不存在的工具，也没有找到原因。有技能时，它找到了两个真正的原因（气隙部件阻碍了滑动网格，以及 JMAG 默认的 1 mm 叠厚），按正确的顺序使用了真实的工具，并提出了数值证明。现在，如果任何文档提到了不存在的工具，测试就会使构建失败。

## 几何留在 JMAG 之外

桥接层有意不自动化 JMAG 的 Geometry Editor。请用可以审阅、版本管理和测试的脚本绘制参数化几何并导出：二维用 DXF（自带的零依赖写入器 `jmag_bridge.dxf.Dxf`），三维用 STEP
（CadQuery、build123d、gmsh、FreeCAD），外加一个标签 JSON，其中包含每个部件的一个内部点、绕组布局和磁钢极性。`jmag_winding_plan` 根据槽数、极数和相数生成该绕组布局。
`examples/spm_motor/make_geometry.py` 是一个完整的参数化 SPM 电机，`examples/spm_motor/run_mcp.py` 通过公开的 MCP 工具驱动整个研究。

## 架构

```
MCP client --stdio--> server.py (Python >= 3.10)       tools, run jobs, orchestration (experiments, cases)
                        |-- worker "main"   --pipes--> worker/ops.py        every JMAG op: validate, act, read back, diff messages
                        |-- worker "runner" (per solve)                    (both on JMAG's own Python 3.8, ProcessLink API)
                        |-- health.py        pre/post rules, physics invariants   (pure, unit-tested)
                        |-- estimate.py winding.py units.py                 analytic layer (pure)
                        |-- dxf.py cad3d.py                                 DXF writer, STEP product/solid name check (pure)
                        |-- plotting.py reporting.py compare.py             post-processing (pure)
                        |-- solver_monitor.py winui.py launcher.py          progress, dialogs, screenshots, detached start
                        `-- docs_index.py                                   search the installed JMAG manual and API
```

求解在一个单独的 runner worker 中进行，它附着在同一个 JMAG 上，因此在长达数小时的运行期间，进度、收敛情况和取消操作始终可用。JMAG 中曾让我们遭遇挂起、崩溃或数值错误的行为，都收集在
[docs/jmag-facts.md](docs/jmag-facts.md)；贡献者说明见 [CLAUDE.md](CLAUDE.md) 和
[CONTRIBUTING.md](CONTRIBUTING.md)。

## 文档

- [docs/quickstart.md](docs/quickstart.md)：安装后的前十分钟
- [docs/install-troubleshooting.md](docs/install-troubleshooting.md)：每种安装或启动症状一节
- [docs/other-clients.md](docs/other-clients.md)：Cursor、Codex、Claude Desktop、VS Code、Windsurf
- [docs/jmag-facts.md](docs/jmag-facts.md)：实测的 JMAG 行为，这些行为曾导致挂起、崩溃或数值错误
- [CONTRIBUTING.md](CONTRIBUTING.md)：如何添加工具或技能

## 开发

```powershell
.venv\Scripts\pip install -e ".[dev,plots]"
.venv\Scripts\python -m pytest          # offline tests: health rules, invariants, estimates, windings, plots, reports, protocol
```

针对真实 JMAG 的在线测试位于 `tests/live/` 和 `examples/spm_motor/run_mcp.py`。CI 在 Python 3.10 和 3.12 上运行离线测试套件，并在 Python 3.8 上运行 worker 协议测试。

## 相关工作

| 项目 | 方法 |
|---|---|
| [jmag-designer-agentic-toolkit](https://github.com/javitc20010326/jmag-designer-agentic-toolkit) | 通过 COM 为 JMAG 18.1 生成脚本；第一个 JMAG 智能体工具包，也是本项目的灵感来源 |
| [pyaedt-mcp](https://github.com/ansys/pyaedt-mcp)、[ansys-aedt-mcp](https://github.com/LaplaceYoung/ansys-aedt-mcp) | Ansys AEDT 的 MCP 封装 |
| [COMSOL_Multiphysics_MCP](https://github.com/wjc9011/COMSOL_Multiphysics_MCP)、[COMSOL-MCP-verifier](https://github.com/yushi-zhou/COMSOL-MCP-verifier) | COMSOL MCP 服务器；verifier 与本项目一样坚持“拿出证明”的立场 |

本项目的不同之处：每次调用都有经过验证的反馈、物理不变量、已知答案自检、安全取消，以及编码了工程师如何判断结果的技能。

## 适用范围和局限

- 仅支持 Windows，因为 JMAG-Designer 的 Python API 和 UI-Automation 辅助程序只在 Windows 上运行。开发和验证均基于
  **JMAG-Designer 23.0**；其他版本可能需要小幅调整（`jmag_self_test` 和 `jmag_capabilities` 会告诉你）。
- docstring 中标为未验证的工具（效率图、优化表、JMAG 绕组定义）是按 API 参考实现的，会回读它们设置的每个值，但尚未在能够运行它们的许可证上运行过。欢迎提供来自其他安装环境的反馈。
- 完整许可证下存在热分析研究类型（`jmag_capabilities` 会列出），但桥接层目前还没有热分析流程；NVH 力导出是 JMAG 提供的脚本。损耗技能记录了这两者的导出途径。
- 在在线测试中，有两个 API 调用使 Designer 23.0 崩溃（JMAG 绕组定义、场值导出）。除非 `force=true`，否则桥接层会拒绝执行它们。
- JMAG 是商业软件。本项目不包含其任何代码或文档。与 JSOL
  Corporation 无隶属关系。JMAG 是 JSOL Corporation 的商标。

## 支持本项目

在一台机器上首次使用时，`jmag_capabilities` 和 `jmag_self_test` 会附带一条一次性的 `first_run_note`，询问你是否愿意在 GitHub 上为该仓库加星。这是自愿选择：除非你回答同意，否则不会加星（同意后通过 GitHub CLI 操作；若 `gh` 不可用，则在浏览器中打开仓库页面）。回答不同意则不会再次询问；该回答保存在状态文件夹（`JMAG_BRIDGE_HOME`）下 `state.json` 的 `star_prompt` 中。

## 许可证

MIT，见 [LICENSE](LICENSE)。
