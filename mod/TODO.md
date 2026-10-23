# 模组进度（先不装，功能齐了再上）

目标：**游戏里的面板和 exe 窗口功能一致**，装不装都能用。

## 已经做完

主面板（「选择世界」右上角 → 「MCMesh 同步」）

- [x] 拉取存档 / 上传存档 / 修复成就权限 / 关闭
- [x] 输出实时滚动（读 `~\.mcmesh\last-run.log`），滚轮翻历史
- [x] 命令跑完自动刷新世界列表

「更多…」面板

- [x] **改我的 ID**（输入框 + 保存，直接写 `~\.mcmesh\config.json`）
- [x] **换角色**（列出 `repo\chars\*.dat`，点一下就跑 `chars --use 名字`）
- [x] **体检**（`doctor`）
- [x] **导出到桌面**（`push --to 桌面\MCMesh存档`）
- [x] **从桌面导入**（`pull --from 桌面\MCMesh存档`）
- [x] **打开备份文件夹**（`~\.mcmesh\backups`）
- [x] **打开 MCMesh 窗口**（等于双击 exe，完整的 exe 功能都在里面）
- [x] 面板上显示"我的 ID / 当前存档 / 已记录角色 / 程序路径"

## 和 exe 窗口对照

| exe 窗口的功能 | 模组里 |
|---|---|
| 上传 / 拉取 | ✅ 主面板 |
| 换角色（以选中身份进入游戏） | ✅ 更多 → 换角色（写完回世界列表进游戏） |
| 改我的 ID | ✅ 更多 → 保存 ID |
| 作弊开关 / 修复成就 | ✅ 主面板「修复成就/权限」 |
| 体检 | ✅ 更多 → 体检 |
| 导出 / 导入存档（文件夹） | ✅ 更多（固定用「桌面\MCMesh存档」） |
| 打开备份 / 备份回滚 | 打开✅，回滚❌ |
| 从旧存档导入玩家数据 | ❌（要选文件夹，模组里不好做） |
| 多存档选择 | ✅ 用游戏自带的世界列表 |
| 窗口里看角色详情（装备/背包） | ❌ |
| 游戏退出自动修复 | ❌（exe 窗口开着时才有） |

## 还没做（按价值）

1. **退出世界时提醒上传**（避免玩完直接关机，进度只在本机）
2. **备份回滚界面**（列出 backups 快照，点一下回滚）
3. **成就/权限状态卡片**（当前多少条成就、允许作弊开没开，不用跑 doctor 才看见）
4. **游戏内看角色详情**（背包/装备，需要读 `.dat` 的 NBT，工作量大）
5. **从旧存档导入**（需要目录选择界面）
6. **进世界前自动 pull**（点存档时先拉最新的）

## 装回去 / 重新构建

```bat
:: 装（想试的时候）
copy "C:\Users\lanld\MCMesh\mod\build\libs\mcmesh-1.0.0.jar" ^
     "C:\Users\lanld\Downloads\.minecraft\versions\Better MC [FABRIC] BMC3\mods\"

:: 构建
cd C:\Users\lanld\MCMesh\mod
set JAVA_HOME=C:\Program Files\Java\jdk-21
"%USERPROFILE%\.mcmesh\tools\gradle-8.10.2\bin\gradle.bat" build
```
