package io.github.mcmesh.client;

import java.util.ArrayList;
import java.util.List;
import java.util.Queue;
import java.util.concurrent.ConcurrentLinkedQueue;

import net.minecraft.client.gui.DrawContext;
import net.minecraft.client.gui.screen.Screen;
import net.minecraft.client.gui.widget.ButtonWidget;
import net.minecraft.text.Text;
import net.minecraft.util.Formatting;

/**
 * MCMesh 面板：四个按钮 + 一个可滚动的输出区。
 * 从「选择世界」界面按右上角的「MCMesh 同步」进入。
 */
public class SyncScreen extends Screen {

    private final Screen parent;
    private final Queue<String> incoming = new ConcurrentLinkedQueue<>();
    private final List<String> lines = new ArrayList<>();

    private String status = "就绪：先「拉取存档」，再进世界；玩完退出后「上传存档」";
    private boolean running = false;
    private int scroll;          // 0 = 最底（最新）

    public SyncScreen(Screen parent) {
        super(Text.literal("MCMesh 同步"));
        this.parent = parent;
    }

    @Override
    protected void init() {
        int w = 92;
        int gap = 6;
        int total = w * 4 + gap * 3;
        int x = (this.width - total) / 2;
        int y = this.height - 42;

        ActionButton(Runner.Action.PULL, x, y, w);
        ActionButton(Runner.Action.PUSH, x + (w + gap), y, w);
        ActionButton(Runner.Action.FIX, x + (w + gap) * 2, y, w);

        this.addDrawableChild(ButtonWidget
                .builder(Text.literal("关闭"), b -> close())
                .dimensions(x + (w + gap) * 3, y, w, 20)
                .build());
    }

    private void ActionButton(Runner.Action action, int x, int y, int w) {
        this.addDrawableChild(ButtonWidget
                .builder(Text.literal(action.label), b -> start(action))
                .dimensions(x, y, w, 20)
                .tooltip(net.minecraft.client.gui.tooltip.Tooltip.of(
                        Text.literal("执行 mcmesh " + action.arg)))
                .build());
    }

    private void start(Runner.Action action) {
        if (running) {
            return;
        }
        running = true;
        scroll = 0;
        status = action.label + " 执行中…";
        Runner.run(action,
                newLines -> incoming.addAll(newLines),
                code -> incoming.add("__DONE__" + Runner.human(code)));
    }

    @Override
    public void render(DrawContext ctx, int mouseX, int mouseY, float delta) {
        this.renderBackground(ctx, mouseX, mouseY, delta);

        // 收后台线程来的输出
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
            try {
                if (parent != null) {
                    parent.resize(client, this.width, this.height);   // 让世界列表重新读盘
                }
            } catch (Throwable ignored) {
                // 老版本没有 resize 也无所谓
            }
        }

        super.render(ctx, mouseX, mouseY, delta);

        ctx.drawCenteredTextWithShadow(this.textRenderer, this.title, this.width / 2, 12, 0xFFFFFF);
        ctx.drawCenteredTextWithShadow(this.textRenderer,
                Text.literal(status).formatted(running ? Formatting.YELLOW : Formatting.GRAY),
                this.width / 2, 28, 0xFFFFFF);
        ctx.drawCenteredTextWithShadow(this.textRenderer,
                Text.literal("程序：" + Runner.describe()).formatted(Formatting.DARK_GRAY),
                this.width / 2, 42, 0xFFFFFF);

        // 输出区
        int left = 20;
        int top = 58;
        int bottom = this.height - 52;
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
                    Text.literal("（还没有输出。先点「拉取存档」。）").formatted(Formatting.DARK_GRAY),
                    left, top, 0xFFFFFF);
        }
    }

    private static String trim(String s, int max) {
        return s.length() <= max ? s : s.substring(0, max - 1) + "…";
    }

    @Override
    public boolean mouseScrolled(double mouseX, double mouseY, double horizontal, double vertical) {
        int maxLines = Math.max(1, (this.height - 52 - 58) / 10);
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
}
