package io.github.mcmesh.client;

import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.screen.v1.ScreenEvents;
import net.fabricmc.fabric.api.client.screen.v1.Screens;
import net.minecraft.client.gui.screen.world.SelectWorldScreen;
import net.minecraft.client.gui.widget.ButtonWidget;
import net.minecraft.text.Text;

/**
 * 客户端入口：在「单人游戏 · 选择世界」界面右上角加一个 MCMesh 按钮。
 *
 * 为什么放这里：换存档/上传都要求游戏**没在存档里**（level.dat 不能被占着），
 * 而"选世界"这个界面正好是"还没进去"的状态，是最合适的时机。
 */
public final class McmeshClient implements ClientModInitializer {

    @Override
    public void onInitializeClient() {
        ScreenEvents.AFTER_INIT.register((client, screen, scaledWidth, scaledHeight) -> {
            if (!(screen instanceof SelectWorldScreen)) {
                return;
            }
            ButtonWidget button = ButtonWidget
                    .builder(Text.literal("MCMesh 同步"), b -> client.setScreen(new SyncScreen(screen)))
                    .dimensions(scaledWidth - 100, 6, 94, 20)
                    .tooltip(net.minecraft.client.gui.tooltip.Tooltip.of(
                            Text.literal("拉取 / 上传存档、修复成就与管理员权限")))
                    .build();
            Screens.getButtons(screen).add(button);
        });
    }
}
