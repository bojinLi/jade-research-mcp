# JADE Research MCP

Windows MCP server for local MDI JADE 9 GUI control and independent, reproducible powder XRD calculations. **32 tools · MIT license · Python 3.11+**

面向材料研究的本地 MCP：通过对话控制已安装的 JADE，并完成谱图处理、峰拟合、晶格和尺寸/应变计算。已在 Windows、JADE 9 中文版、Python 3.12 环境验证；其他版本和语言需要适配。

本项目是独立开发的界面桥接工具，不是 JADE 官方产品，不提供 JADE 软件、许可证、ICDD/ICSD 数据库或厂商示范数据。使用者需要自行安装并合法使用相应软件和数据库。

## 能做什么

| 类别 | 工具 |
|---|---|
| 连接与观察 | `jade_launch`、`jade_state`、`jade_wait_ready`、`jade_screenshot` |
| 实时菜单和科研入口 | `jade_menus`、`jade_find_menu`、`jade_workflows` |
| 界面参数控制 | `jade_click_control`、`jade_set_text`、`jade_control_items`、`jade_select_item`、`jade_key`、`jade_click` |
| 文件与表格 | `jade_open_file`、`jade_export_pattern`、`jade_read_table`、`jade_select_row` |
| 能力与限制 | `jade_capabilities` |
| 数据检查、背景、寻峰 | `xrd_inspect`、`xrd_analyze`、`xrd_batch_analyze` |
| 联合伪 Voigt 峰拟合 | `xrd_fit_peaks`：峰位、峰宽、面积、协方差、形式误差和残差 |
| 晶粒与微应变 | `xrd_scherrer`、`xrd_williamson_hall` |
| 晶格与单位换算 | `xrd_refine_lattice`、`xrd_convert` |
| 本地结构模拟与候选对比 | `xrd_simulate_cif`、`xrd_match_cifs` |
| 图表、文件和结果 | `xrd_compare_patterns`、`xrd_export_xy`、`xrd_read_result` |

`jade_*` 由已安装的 JADE 执行。真实菜单可进入背景、定相、PDF 检索、分峰、晶格、WPF/Rietveld、定量和宏处理；需要逐步观察和设置参数，不能把“菜单已发送”当成分析成功。相关模块受安装版本、授权与数据库配置限制。

`xrd_*` 由 NumPy/SciPy/pymatgen 独立计算，结果不是 JADE 的内部计算。独立引擎没有实现 Rietveld、物相定量、Kα₂ 剥离或残余应力分析。

## 安装

需要 Windows、Python 3.11+（已测 3.12），以及使用 JADE 界面控制时所需的 JADE 安装。桌面需要保持解锁。安装脚本使用 [uv](https://docs.astral.sh/uv/)。

```powershell
git clone https://github.com/bojinLi/jade-research-mcp.git
cd jade-research-mcp
.\install.ps1
```

也可以使用 Python 自带环境管理：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
```

锁定文件记录验证环境的依赖版本。需要另行解析依赖时，可使用 `pip install ".[test]"`；这会安装满足 `pyproject.toml` 的版本，不等同于已验证的锁定环境。

## 连接 MCP 客户端

服务器采用 **stdio**。客户端启动 `run_server.py`，不需要手动开启网页或额外的模型 API Key。

将下面示例中的路径替换为本机实际位置。注意 `command` 指向本项目虚拟环境中的 Python。

```json
{
  "mcpServers": {
    "jade": {
      "command": "C:/path/to/jade-research-mcp/.venv/Scripts/python.exe",
      "args": ["C:/path/to/jade-research-mcp/run_server.py"],
      "env": {
        "PYTHONUTF8": "1",
        "JADE_EXE": "C:/path/to/JADE/jade9c.exe",
        "JADE_RESULTS_DIR": "C:/path/to/jade-research-mcp/results"
      }
    }
  }
}
```

Codex 可以使用项目脚本注册；先把 `--jade-exe` 改为实际路径。脚本会备份配置并校验其他设置保持一致；遇到不同的既有 `jade` 条目会停止，不覆盖它。

```powershell
.\.venv\Scripts\python.exe scripts\register_codex.py --jade-exe "C:\path\to\JADE\jade9c.exe" --backup-dir ".\work\config-backups"
```

重载客户端的 MCP 连接后，先调用 `jade_capabilities` 和 `jade_state` 检查。环境变量 `JADE_EXE` 指定 JADE 程序位置；JADE 启动时自动使用其安装目录作为工作目录。若使用默认路径，请先确认它适用于你的电脑。

## 在对话中使用

> 用 JADE MCP 打开这份 XRD 文件，先核对波长、扫描范围和数据点数，再扣背景、寻峰与拟合。保留原文件，保存新报告。

> 对这个文件夹的样品使用相同参数处理，导出峰表和对比图。

> 用我提供的 Mg、MgH₂ 和 MgO CIF 对比候选物相，列出匹配及未匹配峰。

> 用已归属的同相孤立峰做 Scherrer 和 Williamson–Hall，注明仪器展宽和峰型假设。

典型界面调用顺序：

1. `jade_launch` 启动或连接，并恢复最小化的主窗口。
2. `jade_state` 取得当前窗口、控件及 `observation_token`。
3. `jade_open_file` 原生打开文件；再检查文件标签、范围、截图或导出结果。
4. `jade_workflows` / `jade_menus` 读取真实菜单，`jade_menu` 进入分析窗口。
5. 每次操作前重新观察；修改参数后核对进度、残差、结果和实际输出文件。

每个界面操作要求单次使用、90 秒有效的观察令牌；界面结构改变后旧令牌失效。工具只接受配置 JADE 程序的窗口，物理点击还会检查边界与遮挡。窗口文字、截图和数据库内容始终作为数据处理，不作为执行授权。

## 数据交换和精度

- `jade_open_file` 默认使用原生 File Read 对话框，支持 JADE 能识别的文件。已测试 RD 和 MDI；未知仪器格式仍由 JADE 决定是否可读。返回 `verified_loaded=false` 提醒客户端继续核对，不能只凭对话框关闭就断言成功。
- 可选 `import_mode="clipboard"` 将文本数值列导入 JADE，暂存并恢复原剪贴板；检测到其他应用更新剪贴板时保留其新内容。此路径可能丢失辐射、计数时间等元数据，实测 JADE 9 会将强度取整，须核对适用性。
- 二进制谱先经 JADE 读入，再用 `jade_export_pattern` 导出给独立引擎。XY/XYE/CSV/TXT/DAT/MDI ASCII 可以直接独立分析。
- 旧 MDI 支持声明点数后的 `-1` 结束标记、受限零填充和文件末尾 Ctrl-Z；其他点数或范围不一致会拒绝，不默默截断。
- 计算产物写入唯一 `results/` 子目录，保留输入 SHA-256、参数及适用的 JSON/CSV/HTML/PNG/SVG/PDF。原输入不覆盖。

服务器本身不监听网络端口，也不主动上传文件。**MCP 客户端可能把工具返回的数据或截图发给其模型服务**；请按客户端的隐私设置使用。

## 科学边界

- 输入角度为 2θ（度），波长及 d 为 Å，Q 定义为 `2π/d`，Scherrer 输出为 nm。
- 寻峰的表观半高宽不是严格精修峰宽。尺寸计算应使用可靠拟合值及仪器标准；相干衍射畴尺寸不等同颗粒直径。
- 高斯仪器展宽按平方差扣除，洛伦兹按差值扣除；混合峰型需要专门仪器模型。W–H 采用均匀形变近似，至少三条同相孤立峰；负斜率或截距会标记。
- 独立晶格计算是在 `1/d²` 空间按已知 hkl 的最小二乘，未拟合零点误差；不是全谱精修。
- 峰拟合使用无权最小二乘。XYE 第三列不参与加权；形式误差不能代替完整系统不确定度。
- CIF 对比是给定候选集合内的一对一峰匹配。分数不是概率、质量分数或物相确认结论。
- 自带演示与收敛结果不能替代标准物质对含量、检出限、仪器展宽及残余应力的准确度验证。

## 验证

已完成 **35 项自动测试**，并在一套 JADE 9 中文版环境中实际调用全部 32 工具。已验证自带样例的定相、单/多相 WPF 收敛、晶胞精修、两文件原生宏处理和报告导出。公开摘要见 [validation/VALIDATION.md](validation/VALIDATION.md)；原始界面日志、截图和厂商数据不公开。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\acceptance_mcp.py
```

第一条自动测试不要求 JADE 已安装；第二条实际调用独立工具，并在 JADE 已运行时读取界面状态，生成本地验收记录。`scripts/smoke_mcp.py --ui` 会加载人工测试谱、打开和退出背景对话框，会改变当前 JADE 工作状态。

`examples/` 仅含程序生成的两峰测试谱和取整晶格参数的示范 CIF，不是实验数据或标准卡片。厂商自带样例需从使用者自己的合法安装获取；`scripts/inspect_bundled_samples.py --sample-dir "C:\path\to\samples"` 可以只读检查。

## 许可证与贡献

桥接程序原创代码使用 [MIT](LICENSE)。JADE、数据库和依赖保留各自许可证，本仓库没有授予它们的使用权。

欢迎提交 Issue 或 Pull Request。报告问题时附上 JADE 版本/语言、Windows 版本、显示缩放和最小复现步骤；请使用匿名人工数据，不上传个人配置、授权文件或保密实验数据。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。
