# 模组待完善清单（先不装，功能齐了再上）

现状：模组壳子已经能跑通了（Fabric 客户端模组，世界列表右上角「MCMesh 同步」按钮，
面板里 拉取 / 上传 / 修复成就权限 / 关闭，输出实时滚动）。**暂时没装进整合包**。

## 已经有的

- [x] 世界列表加「MCMesh 同步」按钮（`ScreenEvents.AFTER_INIT`，无 mixin）
- [x] 面板：拉取 / 上传 / 修复 / 关闭 + 可滚动输出
- [x] 调用本机 `MCMesh.exe`（自动找桌面/MCMesh 目录，支持 `MCMESH_EXE` 环境变量）
- [x] 输出跟随 `%USERPROFILE%\.mcmesh\last-run.log`
- [x] 跑完自动刷新世界列表

## 待做（按价值排序，等你挑）

1. **面板里显示"我现在是谁"**
   读 `~/.mcmesh/config.json` 和 `repo/players.json`：我的 ID、当前存档、上次上传人和时间、
   以及"本地比远端新 / 旧"的提示 —— 现在这些只能在 `status` 输出里看。

2. **退出世界时提醒上传**
   监听世界卸载事件，如果这次玩过（存档指纹变了）就发一个 toast / 面板提示"该上传了"，
   避免玩完直接关机导致进度只在本机。

3. **一键开局（拉取 → 进世界）**
   在世界列表点某个存档时，先 `pull` 再进，省掉"先点拉取再双击世界"。

4. **备份 / 回滚界面**
   列出 `~/.mcmesh/backups/` 里的快照（level.dat 备份 + before-pull zip），
   点一下回滚到某个时间点。

5. **好友状态**
   从 `repo/players.json` + 最近 push 记录里显示"谁最后玩的 / 谁该接棒了"。

6. **成就/权限体检卡片**
   把 `fix` 的结果做成卡片：现在多少条成就、允许作弊开没开、一键修。

7. **中英双语 / 配置界面**
   面板里直接改 ID、exe 路径、存档目录，不用去点 exe 窗口。

## 装回去的方法（想试的时候）

```bat
copy "C:\Users\lanld\MCMesh\mod\build\libs\mcmesh-1.0.0.jar" ^
     "C:\Users\lanld\Downloads\.minecraft\versions\Better MC [FABRIC] BMC3\mods\"
```

改完代码重新构建：

```bat
cd C:\Users\lanld\MCMesh\mod
set JAVA_HOME=C:\Program Files\Java\jdk-21
"%USERPROFILE%\.mcmesh\tools\gradle-8.10.2\bin\gradle.bat" build
```
