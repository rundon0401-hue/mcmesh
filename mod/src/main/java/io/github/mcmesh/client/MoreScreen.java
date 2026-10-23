package io.github.mcmesh.client;

import java.awt.Desktop;
import java.io.File;
import java.util.ArrayList;
import java.util.List;

import net.minecraft.client.gui.screen.Screen;
import net.minecraft.client.gui.widget.ButtonWidget;
import net.minecraft.client.gui.widget.TextFieldWidget;
import net.minecraft.text.Text;
import net.minecraft.util.Formatting;

/**
 * 「更多」：把 exe 窗口能做、但主面板没放的事都放这儿
 * —— 改我的 ID、换角色、体检、导出/导入、打开备份、打开 exe 窗口。
 */
public class MoreScreen extends ConsoleScreen {

    private TextFieldWidget idField;
    private String savedHint = "";

    public MoreScreen(Screen parent) {
        super(parent, "MCMesh · 更多功能");
        this.status = "我的 ID：" + (Store.playerId().isEmpty() ? "（还没设）" : Store.playerId())
                + "    存档：" + (Store.worldName().isEmpty() ? "（还没设）" : Store.worldName());
    }

    @Override
    protected void buildWidgets() {
        int gap = 6;
        int w = 110;
        int h = 20;

        // 第一行：改我的 ID
        int rowY = 52;
        idField = new TextFieldWidget(this.textRenderer, 20, rowY, 160, h, Text.literal("我的 ID"));
        idField.setText(Store.playerId());
        idField.setMaxLength(16);
        this.addDrawableChild(idField);
        this.addDrawableChild(ButtonWidget.builder(Text.literal("保存 ID"), b -> saveId())
                .dimensions(186, rowY, 80, h).build());
        this.addDrawableChild(ButtonWidget.builder(Text.literal("换角色…"), b ->
                        this.client.setScreen(new CharScreen(this)))
                .dimensions(272, rowY, 80, h).build());

        // 第二行：常用命令
        int y2 = rowY + h + gap;
        addButton("体检", 20, y2, w, h, () -> exec(Runner.Action.DOCTOR));
        addButton("导出到桌面", 20 + (w + gap), y2, w, h, () -> exec(Runner.Action.EXPORT));
        addButton("从桌面导入", 20 + (w + gap) * 2, y2, w, h, () -> exec(Runner.Action.IMPORT));
        addButton("打开备份", 20 + (w + gap) * 3, y2, w, h, this::openBackups);

        // 第三行：窗口 & 返回
        int y3 = y2 + h + gap;
        addButton("打开 MCMesh 窗口", 20, y3, 150, h, this::openGuiWindow);
        addButton("返回", 20 + 156, y3, 80, h, this::close);
    }

    @Override
    protected int outputTop() {
        return 132;
    }

    private void saveId() {
        String id = idField.getText().trim();
        if (id.isEmpty()) {
            savedHint = "ID 不能是空的";
            return;
        }
        if (id.chars().anyMatch(Character::isWhitespace)) {
            savedHint = "ID 里不能有空格";
            return;
        }
        if (Store.setPlayerId(id)) {
            savedHint = "已保存：" + id + "（新身份，相当于换了个角色）";
            status = "我的 ID 已改成 " + id;
        } else {
            savedHint = "写不进去：" + Store.configFile();
        }
    }

    private void openBackups() {
        try {
            File f = Store.backupsDir().toFile();
            if (!f.isDirectory()) {
                f.mkdirs();
            }
            if (Desktop.isDesktopSupported()) {
                Desktop.getDesktop().open(f);
                savedHint = "已打开：" + f;
            }
        } catch (Exception e) {
            savedHint = "打不开：" + e.getMessage();
        }
    }

    private void openGuiWindow() {
        savedHint = Runner.openGui()
                ? "已让 MCMesh 窗口启动（会出现在任务栏）"
                : "没找到 MCMesh.exe，设置环境变量 MCMESH_EXE 指给它";
    }

    @Override
    protected void drawOutput(net.minecraft.client.gui.DrawContext ctx) {
        super.drawOutput(ctx);
        if (!savedHint.isEmpty()) {
            ctx.drawTextWithShadow(this.textRenderer,
                    Text.literal(savedHint).formatted(Formatting.GREEN), 20, outputTop() - 12, 0xFFFFFF);
        }
        List<String> chars = Store.characters();
        String line = "已记录角色：" + (chars.isEmpty() ? "（还没有）" : String.join("、", chars));
        ctx.drawTextWithShadow(this.textRenderer,
                Text.literal(trim(line, 118)).formatted(Formatting.GRAY), 20, outputTop() - 24, 0xFFFFFF);
    }
}
