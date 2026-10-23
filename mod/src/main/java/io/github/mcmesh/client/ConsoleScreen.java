package io.github.mcmesh.client;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;

import net.minecraft.client.gui.DrawContext;
import net.minecraft.client.gui.screen.Screen;
import net.minecraft.client.gui.widget.ButtonWidget;
import net.minecraft.text.Text;
import net.minecraft.util.Formatting;

/**
 * 通用"跑一条命令并把输出贴在面板上"的界面。
 *
 * MCMesh 面板（同步）、换角色、更多功能都继承它，省得每个界面重写一遍输出区。
 */
public abstract class ConsoleScreen extends Screen {

    protected final Screen parent;
    private final Consumer<List<String>> sink = lines -> {
    };

    protected final List<String> lines = new ArrayList<>();
    private final java.util.Queue<String> incoming = new java.util.concurrent.ConcurrentLinkedQueue<>();

    protected String status = "";
    protected boolean running;
    protected int scroll;

    protected ConsoleScreen(Screen parent, String title) {
        super(Text.literal(title));
        this.parent = parent;
    }

    /** 子类决定底部/中部放什么按钮。 */
    protected abstract void buildWidgets();

    /** 输出区顶部留多少高度（子类按自己的按钮布局调）。 */
    protected int outputTop() {
        return 58;
    }

    /** 输出区底部留多少高度（给按钮行）。 */
    protected int outputBottomGap() {
        return 52;
    }

    @Override
    protected void init() {
        buildWidgets();
    }

    /** 跑一条命令；结果贴到输出区。 */
    protected void exec(String label, java.util.List<String> args) {
        if (running) {
            return;
        }
        running = true;
        scroll = 0;
        status = label + "：执行中…";
        Runner.runArgs(label, args,
                newLines -> incoming.addAll(newLines),
                code -> incoming.add("__DONE__" + Runner.human(code)));
    }

    protected void exec(Runner.Action action) {
        if (running) {
            return;
        }
        running = true;
        scroll = 0;
        status = action.label + "：执行中…";
        Runner.run(action,
                newLines -> incoming.addAll(newLines),
                code -> incoming.add("__DONE__" + Runner.human(code)));
    }

    protected void drain() {
        String done = null;
        String line;
        while ((line = incoming.poll()) != null) {
            if (line.startsWith("__DONE__")) {
                done = line.substring("__DONE__".length());
            } else {
                lines.add(line);
                if (lines.size() > 500) {
                    lines.remove(0);
                }
            }
        }
        if (done != null) {
            running = false;
            status = done;
            onFinished();
        }
    }

    /** 命令跑完后的收尾（默认什么也不做）。 */
    protected void onFinished() {
    }

    protected void drawOutput(DrawContext ctx) {
        int left = 20;
        int top = outputTop();
        int bottom = this.height - outputBottomGap();
        int lineHeight = 10;
        int maxLines = Math.max(1, (bottom - top) / lineHeight);

        int end = Math.max(0, lines.size() - scroll);
        int startIdx = Math.max(0, end - maxLines);
        int y = top;
        for (int i = startIdx; i < end; i++) {
            String text = lines.get(i);
            int color = text.startsWith("$ ") ? 0x6B7280
                    : text.startsWith("[X") ? 0xFF6B6B
                    : text.startsWith("[!") ? 0xFFD166
                    : text.startsWith("[OK") ? 0x4ADE80
                    : 0xC9D1DB;
            ctx.drawTextWithShadow(this.textRenderer, Text.literal(trim(text, 118)), left, y, color);
            y += lineHeight;
        }
        if (lines.isEmpty()) {
            ctx.drawTextWithShadow(this.textRenderer,
                    Text.literal("（还没有输出）").formatted(Formatting.DARK_GRAY), left, top, 0xFFFFFF);
        }
        ctx.drawCenteredTextWithShadow(this.textRenderer,
                Text.literal(status).formatted(running ? Formatting.YELLOW : Formatting.GRAY),
                this.width / 2, 28, 0xFFFFFF);
        ctx.drawCenteredTextWithShadow(this.textRenderer,
                Text.literal("程序：" + Runner.describe()).formatted(Formatting.DARK_GRAY),
                this.width / 2, 42, 0xFFFFFF);
    }

    @Override
    public void render(DrawContext ctx, int mouseX, int mouseY, float delta) {
        drain();
        this.renderBackground(ctx, mouseX, mouseY, delta);
        super.render(ctx, mouseX, mouseY, delta);
        ctx.drawCenteredTextWithShadow(this.textRenderer, this.title, this.width / 2, 12, 0xFFFFFF);
        drawOutput(ctx);
    }

    @Override
    public boolean mouseScrolled(double mouseX, double mouseY, double horizontal, double vertical) {
        int lineHeight = 10;
        int maxLines = Math.max(1, (this.height - outputBottomGap() - outputTop()) / lineHeight);
        int maxScroll = Math.max(0, lines.size() - maxLines);
        scroll = Math.max(0, Math.min(maxScroll, scroll + (int) Math.signum(vertical) * 2));
        return true;
    }

    @Override
    public void close() {
        this.client.setScreen(parent);
    }

    @Override
    public boolean shouldPause() {
        return false;
    }

    protected static String trim(String s, int max) {
        return s.length() <= max ? s : s.substring(0, max - 1) + "…";
    }

    protected ButtonWidget button(String label, int x, int y, int w, int h, Runnable onClick) {
        return ButtonWidget.builder(Text.literal(label), b -> onClick.run())
                .dimensions(x, y, w, h).build();
    }

    protected void addButton(String label, int x, int y, int w, int h, Runnable onClick) {
        this.addDrawableChild(button(label, x, y, w, h, onClick));
    }
}
