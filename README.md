# Bond Interest-Bearing Debt Summary

本项目用于从债券年度报告/募集说明书 PDF 中提取“发行人合并口径有息债务结构情况”，并在本地网页中进行核验、编辑与导出。

## 快速开始（本地）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

打开 `http://127.0.0.1:5000/`。

## Docker（推荐用于部署）

```bash
docker compose up --build
```

打开 `http://localhost:5000/`。

## 目录说明

- `app.py`：Flask 服务与 API
- `modules/`：解析、OCR、聚合、导出逻辑
- `templates/` `static/`：交互页面
- `uploads/`：上传 PDF（默认不入库）

## 文档

完整项目说明见 [docs/项目说明-有息债务提取与汇总工具.md](docs/项目说明-有息债务提取与汇总工具.md)。

运行与验收要点见 [docs/债务提取平台-运行与验收.md](docs/债务提取平台-运行与验收.md)。

