# Creo Assembly SOP Agent

This context describes the language shared by BOM understanding, Creo assembly planning, image generation, clarification, and SOP delivery.

## Language

**主工序（Main Process）**:
BOM 中用于组织装配工作的工序分组；一个主工序可以包含多个安装步骤并跨多个 SOP 页面。
_Avoid_: 大步骤、BOM步骤

**安装步骤（Installation Step）**:
一次可独立生成、复核和重新生成的原子装配动作。
_Avoid_: 小步骤、图片任务

**渲染任务（Render Job）**:
为一个安装步骤生成某一正式视图或候选视图的工作项。
_Avoid_: 安装步骤、工序

**运行批次（Run）**:
从一份 BOM 和一个 CAD 文件夹开始，直到交付或系统阻断的一次完整处理。
_Avoid_: 会话、聊天、任务

**生成方案（Plan Revision）**:
用户在生成前确认的、具有明确版本的主工序、安装步骤、装配关系和选择集合。
_Avoid_: 临时计划、Qwen答案

**释疑项（Clarification Item）**:
生成前发现的、需要记录唯一处理选择的装配语义问题。
_Avoid_: 报错、提示

**疑惑步骤（Questioned Step）**:
已经产生可复核结果，但仍有一个或多个合理解释尚未由用户确认的安装步骤。
_Avoid_: 失败步骤、错误图片

**失败步骤（Failed Step）**:
在有界自动恢复后仍无法产生满足基础硬门结果的安装步骤。
_Avoid_: 疑惑步骤

**候选图（Candidate Image）**:
疑惑步骤下通过基础硬门、仅在待释疑因素上不同的备选安装图。
_Avoid_: 失败图片、随机重跑图片

**步骤修订（Step Revision）**:
用户释疑后，对一个安装步骤及其真实依赖范围形成的新版本。
_Avoid_: 手工补丁、覆盖原图

**完整安装态（Complete State）**:
某安装步骤完成后，权威总装中已安装 occurrence 的确定状态。
_Avoid_: 已出图状态、上一张图片

**交付结果（Delivery）**:
提供给用户的最新 SOP 和步骤图片集合；内部合同、日志和审计资料不属于交付结果。
_Avoid_: 运行目录、输出缓存

