package io.github.mcmesh.client;

import java.util.List;

import net.minecraft.client.gui.screen.Screen;

/**
 * 主面板：拉取 / 上传 / 修复 / 更多 / 关闭。
 * 从「选择世界」界面右上角的「MCMesh 同步」进入。
 */
public class SyncScreen extends ConsoleScreen {

    public SyncScreen(Screen parent) {
        super(parent, "MCMesh 同步");
        this.status = "就绪：先「拉取存档」，再进世界；玩完退出后「上传存档」";
    }

    @Override
    protected void buildWidgets() {
        int gap = 6;
        int y = this.height - 42;
        int total = 4;
        int w = Math.min(96, (this.width - 40 - gap * (total - 1)) / total);
        int x = (this.width - (w * total + gap * (total - 1))) / 2;

        addButton("拉取存档", x, y, w, 20, () -> exec(Runner.Action.PULL));
        addButton("上传存档", x + (w + gap), y, w, 20, () -> exec(Runner.Action.PUSH));
        addButton("修复成就/权限", x + (w + gap) * 2, y, w, 20, () -> exec(Runner.Action.FIX));
        addButton("更多…", x + (w + gap) * 3, y, w, 20,
                () -> this.client.setScreen(new MoreScreen(this)));
    }

    @Override
    protected void onFinished() {
        try {
            if (parent != null) {
                parent.resize(client, this.width, this.height);   // 让世界列表重新读盘
            }
        } catch (Throwable ignored) {
            // 老版本没有 resize 也无所谓
        }
    }
}
