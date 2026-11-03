# 我的应用

Cherryfxt 的独立应用导航页。仅汇总已核对可用的 GitHub Pages 应用，不复制应用代码、不读取或写入其他应用的进度。

## 当前应用

- 出发吧！：https://cherryfxt.github.io/chufaba-english-pages/
- 背诵练习室：https://cherryfxt.github.io/recitation-room/
- 三词成故事：https://cherryfxt.github.io/three-word-story/
- 成语画猜：https://cherryfxt.github.io/idiom-draw-guess/

2026-11-02 核对：上述 4 个应用页面均返回 HTTP 200。

## 维护

在 apps.json 增加或修改应用名称、说明、分类和链接。页面支持关键词搜索、用途筛选与移动端显示。无构建步骤，无登录、后端、Cookie 或本地存储。

本地运行：python3 -m http.server 8080

## 卡片视觉与图标来源

- 出发吧！：使用线上应用的 apple-touch-icon.png 原图、garage-dawn.png 原背景，保留深色与琥珀色风格。
- 成语画猜：使用线上应用的 app-icon-512.png 原图，卡片配图沿用米色纸面、深绿海报和砖红点缀。
- 三词成故事：原网页未提供独立桌面图标，本页以原有黄、绿、紫三色词卡整理图标；卡片预览沿用原页面的三色词卡、衬线标题和计时样式。
- 背诵练习室：原网页未提供独立桌面图标，本页以原页眉“记”字标识整理图标；配图沿用米白底、深绿文字与挖空槽位。

此修改仅用于汇总页，不修改各应用自身图标或线上部署。

## 自动收录与发布

统一入口：https://cherryfxt.github.io/app-hub/

GitHub Actions 每小时检查一次 Cherryfxt 的公开仓库，也可以在 Actions → Discover apps and publish hub → Run workflow 手动刷新。新仓库的 GitHub Pages 启用且首页可访问后，会自动出现在列表；普通代码仓库、私有仓库、无法访问的页面和汇总页自身不会作为新应用加入。添加仓库主题 `app-hub-ignore` 可将该仓库排除在自动发现之外。

现有四张定制卡片由 `apps.config.json` 管理。新卡片优先读取应用网页标题、描述和图标，默认归为“其他应用”。发布前由 `scripts/discover_apps.py` 生成站点目录和核对时间，网页不直接调用 GitHub API。自动更新只发布本汇总页，不写入任何其他仓库。无需设置个人令牌；工作流使用 GitHub 提供的临时令牌。

本地验证：`python3 -m unittest discover -s tests -v`。线上自动发布由 `.github/workflows/publish.yml` 管理；GitHub 可能延迟运行定时任务。
