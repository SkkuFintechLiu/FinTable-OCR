# FinTable-OCR · 有息债务提取与汇总

从债券年度报告和募集说明书 PDF 中提取“发行人合并口径有息债务结构情况”，通过本地网页核验、编辑并导出 Excel。

适用于需要批量整理发行人年度债务结构的研究与数据核验工作。自动抽取结果需要结合原报告确认年份、合并口径与单位。

## 能做什么

- 上传 PDF，查看处理队列、发行人、年份与文件类型，必要时改类型重跑。
- 解析文本表格，并通过 OCR 处理扫描内容。
- 汇总信用类债券、银行贷款、非银行金融机构贷款、其他有息债务、合计与一年以内债务，统一输出为亿元。
- 在网页中校对单元格，导出包含汇总与追溯信息的 Excel。

## 本地启动

Docker 镜像采用 Python 3.11，本地环境也建议先使用该版本。

```bash
git clone https://github.com/SkkuFintechLiu/FinTable-OCR.git
cd FinTable-OCR
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

Windows 使用 `py -3.11 -m venv .venv` 创建环境，并用 `.venv\Scripts\activate` 激活。启动后访问 [本地页面](http://127.0.0.1:5000/)。

首次使用 OCR 可能需要下载模型。依赖安装或模型初始化失败时，请保留终端报错并检查 PaddleOCR / PaddlePaddle 与操作系统的兼容性。

## Docker 启动

在仓库根目录执行：

```bash
docker compose up --build
```

访问 [本地页面](http://localhost:5000/)。Compose 将 `uploads/`、`build/` 和 `.cache/` 映射到本机，分别保存上传文件、构建/运行产物及缓存。

## 第一次使用

1. 上传一份有代表性的 PDF，等待队列处理完成。
2. 对照原报告核查发行人、年度、合并口径与金额单位。
3. 查看追溯信息中的页码与命中标题，修正识别结果。
4. 确认汇总数据后导出 Excel，再扩大到批量处理。

扫描件耗时通常更长；未找到表格或可信度低时，需要人工核对或补充解析规则。不要把未命中当作零债务。

## 文件导航

| 路径 | 用途 |
| --- | --- |
| `app.py` | Flask 服务与 API |
| `modules/` | PDF 解析、OCR、聚合、追溯与 Excel 导出 |
| `templates/`、`static/` | 网页界面 |
| `scripts/` | 样本验证与上传文件索引脚本 |
| `packaging/` | macOS / Windows 打包配置 |
| `docs/` | 项目说明、运行与验收文档 |

## 进一步阅读

- [完整项目说明](docs/项目说明-有息债务提取与汇总工具.md)
- [运行与验收要点](docs/债务提取平台-运行与验收.md)
- [独立软件打包与启动](启动说明.md)

上传文件的保留方式取决于运行模式。普通源码运行的 `uploads/` 不会按打包软件规则自动清理；打包模式及清理机制见启动说明。请按实际数据管理要求处理本地文件。
