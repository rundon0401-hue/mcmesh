package io.github.mcmesh.client;

import java.util.List;

import net.minecraft.client.gui.screen.Screen;

/**
 * 换角色：把 chars\ 里记录的角色列出来，点一个就把那具身体写进当前存档（= exe 的「以选中身份进入游戏」）。
 *
 * 注意：这一步只改 level.dat，改完要回到世界列表点存档进游戏。
 */
public class CharScreen extends ConsoleScreen {

    public CharScreen(Screen parent) {
        super(parent, "MCMesh · 换角色");
        this.status = "点一个角色 → 写进当前存档 → 回去点存档进游戏";
    }

    @Override
    protected void buildWidgets() {
        List<String> chars = Store.characters();
        int gap = 6;
        int w = 150;
        int h = 20;
        int cols = Math.max(1, (this.width - 40 + gap) / (w + gap));
        int x0 = 20;
        int y0 = 52;

        if (chars.isEmpty()) {
            status = "chars 里还没有角色 —— 先在 exe 窗口里玩一局或上传一次";
        }
        for (int i = 0; i < chars.size() && i < 24; i++) {
            String name = chars.get(i);
            int col = i % cols;
            int row = i / cols;
            addButton(name, x0 + col * (w + gap), y0 + row * (h + gap), w, h,
                    () -> switchTo(name));
        }
        int rows = Math.max(1, (chars.size() + cols - 1) / cols);
        int yBack = y0 + rows * (h + gap) + 4;
        addButton("返回", x0, yBack, 80, h, this::close);
    }

    /** 按钮多了就把输出区往下压，免得挡住。 */
    @Override
    protected int outputTop() {
        int rows = Math.max(1, (Store.characters().size() + 3) / 4);
        return 52 + rows * 26 + 34;
    }

    private void switchTo(String name) {
        List<String> args = List.of("chars", "--use", name);
        exec("换成「" + name + "」", args);
    }

    @Override
    protected void onFinished() {
        // 换完角色，世界列表不用刷新（level.dat 变了，进世界就会用新的）
    }
}
