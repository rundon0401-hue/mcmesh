package io.github.mcmesh.client;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

/**
 * 读写 MCMesh 的用户数据（都在 %USERPROFILE%\.mcmesh 下），全部是纯文本，方便和 exe 共用。
 */
public final class Store {

    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();

    private Store() {
    }

    public static Path dataDir() {
        return Path.of(System.getProperty("user.home"), ".mcmesh");
    }

    public static Path configFile() {
        return dataDir().resolve("config.json");
    }

    public static Path repoDir() {
        return dataDir().resolve("repo");
    }

    public static Path charsDir() {
        return repoDir().resolve("chars");
    }

    public static Path playersFile() {
        return repoDir().resolve("players.json");
    }

    public static Path backupsDir() {
        return dataDir().resolve("backups");
    }

    /** 读 config.json（没有就返回空对象）。 */
    public static JsonObject config() {
        try {
            if (Files.isRegularFile(configFile())) {
                String s = Files.readString(configFile(), StandardCharsets.UTF_8);
                return JsonParser.parseString(s).getAsJsonObject();
            }
        } catch (Exception ignored) {
            // 文件坏了就当没有
        }
        return new JsonObject();
    }

    public static String playerId() {
        JsonObject c = config();
        return c.has("player") ? c.get("player").getAsString() : "";
    }

    public static String worldName() {
        JsonObject c = config();
        return c.has("world") ? c.get("world").getAsString() : "";
    }

    /** 改「我的 ID」：只动 config.json 里的 player 字段，其它原样保留。 */
    public static boolean setPlayerId(String id) {
        try {
            JsonObject c = config();
            c.addProperty("player", id);
            Files.createDirectories(configFile().getParent());
            Files.writeString(configFile(), GSON.toJson(c), StandardCharsets.UTF_8);
            return true;
        } catch (IOException e) {
            return false;
        }
    }

    /** 已记录的角色（chars 文件夹里的 .dat，按名字排序）。 */
    public static List<String> characters() {
        List<String> out = new ArrayList<>();
        File[] files = charsDir().toFile().listFiles((d, n) -> n.endsWith(".dat"));
        if (files == null) {
            return out;
        }
        for (File f : files) {
            String n = f.getName();
            out.add(n.substring(0, n.length() - 4));
        }
        out.sort(String.CASE_INSENSITIVE_ORDER);
        return out;
    }

    public static boolean hasBackups() {
        File[] files = backupsDir().toFile().listFiles();
        return files != null && files.length > 0;
    }

    /** 导出用的默认文件夹（桌面\MCMesh存档）。 */
    public static Path exportDir() {
        return Path.of(System.getProperty("user.home"), "Desktop", "MCMesh存档");
    }
}
