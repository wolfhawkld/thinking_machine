# 英文预印本排版检查（2026-09-10）

## 交付与页数

- [正文PDF](../paper-build-20260910/paper-manuscript-en-20260910.pdf)：共7页；标题、摘要至结论占6页，参考文献与可用性说明占第7页。TeX的main-end标签落在第6页，PDF文本核对一致。
- [补充材料PDF](../paper-build-20260910/paper-supplement-en-20260910.pdf)：4页。
- 使用中性A4单栏、10pt DejaVu Serif、19mm页边距，未冒用会议／期刊模板。表格用small字号，正文保持10pt；调整的是标题间距与段落留白，未删改实验内容来压页数。

原第一版为正文7页＋参考1页。调整章节空白后为正文6页＋参考1页，因此在当前中性排版下达到原先5–6页正文目标；不保证换会议模板或页数口径后仍达标。补充稿最后一页较短，保留内容和正常字号，不为凑整页改变证据。

## 检查

XeLaTeX运行两遍，均成功，无Overfull、Missing character或Undefined control警告。PyMuPDF读取全部11页，未发现替代缺字符或超出页面边界的词；逐页接触图已视觉检查，未见表格或正文相互遮挡。正文三张表和补充材料短表不跨页拆分；长表具重复表头支持。

PDF中的文献链接保留外部URL。本地仓库链接在PDF中只保留文字，具体相对路径仍在Markdown中；不生成指向用户本机的失效公开链接。页首保留working draft标识，没有作者／机构的虚构信息。正文内部的编辑状态说明在PDF中压缩为标准草稿标签，可用性结尾保留尚未公开完整复现包的声明。

## 重建

```bash
python3 reviews/render_english_preprint_20260910.py
PYTHONPATH=/tmp/thinking-machine-pdf-check-20260910 python3 reviews/check_english_pdf_20260910.py
```

排版脚本只需标准库和现有XeLaTeX及字体；PDF检查脚本需要PyMuPDF和Pillow。本次经许可将PyMuPDF安装到/tmp/thinking-machine-pdf-check-20260910，未改项目依赖；临时目录丢失后需重新准备检查依赖，不能假定换机即存在。

产物目录包含可重建TeX、PDF、编译日志、逐页检查JSON和接触图。脚本仅写此目录，不调用模型、oracle或冻结分析，不改Markdown源及实验材料。本轮未提交／推送／上传。

下一步：作者信息、最终模板／目标渠道与公开材料清单仍需确定；完整提示及逐world可分发导出、去凭证复现包和独立环境验证尚待做。当前是可阅读的英文预印本草稿，不是已完成投稿或公开发布。
