package io.github.mcmesh.client;

import java.io.File;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.function.Consumer;

/**
 * 调用本机的 MCMesh 程序（exe 或 .py），把输出（exe 会写到 ~/.mcmesh/last-run.log）读回来。
 *
 * 之所以读日志文件而不是 stdout：exe 是 --windowed 打包的，没有控制台，print 会写进那个日志。
 */
public final class Runner {

    /** 面板上的快捷动作。 */
    public enum Action {
        PULL("拉取存档", "pull"),
        PUSH("上传存档", "push"),
        FIX("修复成就/权限", "fix"),
        DOCTOR("体检", "doctor"),
        EXPORT("导出到文件夹", "push", "--to", ""),
        IMPORT("从文件夹导入", "pull", "--from", "");

        public final String label;
        public final List<String> args;

        Action(String label, String... args) {
            this.label = label;
            this.args = List.of(args);
        }
    }

    private Runner() {
    }

    public static Path logFile() {
        return Path.of(System.getProperty("user.home"), ".mcmesh", "last-run.log");
    }

    /** 找到本机的 MCMesh 程序；找不到返回 null。 */
    public static List<String> resolve() {
        String env = System.getenv("MCMESH_EXE");
        if (env != null && !env.isBlank() && new File(env).isFile()) {
            return List.of(env);
        }
        String home = System.getProperty("user.home");
        String[] exes = {
                home + "\\Desktop\\MCMesh.exe",
                home + "\\MCMesh\\MCMesh.exe",
                home + "\\MCMesh\\dist\\MCMesh.exe",
                home + "\\Downloads\\MCMesh.exe"
        };
        for (String p : exes) {
            if (new File(p).isFile()) {
                return List.of(p);
            }
        }
        String[] scripts = {
                home + "\\MCMesh\\mcmesh.py",
                home + "\\Desktop\\MCMesh\\mcmesh.py"
        };
        for (String p : scripts) {
            if (new File(p).isFile()) {
                String py = new File("C:\\Windows\\py.exe").isFile() ? "py" : "python";
                return List.of(py, "-3", p);
            }
        }
        return null;
    }

    public static String describe() {
        List<String> cmd = resolve();
        return cmd == null ? "（没找到 MCMesh.exe）" : String.join(" ", cmd);
    }

    /** 打开 MCMesh 的窗口界面（等于双击 exe）。 */
    public static boolean openGui() {
        List<String> cmd = resolve();
        if (cmd == null) {
            return false;
        }
        try {
            // 如果是 py 启动器，去掉 -3 script，只留 python 可执行文件跑 GUI 不合适；
            // 这种情况就直接跑 exe —— 找不到就返回 false。
            String exe = cmd.get(cmd.size() - 1).toLowerCase(Locale.ROOT).endsWith(".exe")
                    ? cmd.get(cmd.size() - 1) : null;
            if (exe == null) {
                return false;
            }
            new ProcessBuilder(exe).directory(new File(System.getProperty("user.home"))).start();
            return true;
        } catch (IOException e) {
            return false;
        }
    }

    public static void run(Action action, Consumer<List<String>> onLines, Consumer<Integer> onDone) {
        List<String> args = new ArrayList<>(action.args);
        if (action == Action.EXPORT || action == Action.IMPORT) {
            // 这两个要带一个文件夹参数（默认「桌面\MCMesh存档」）
            String dir = Store.exportDir().toString();
            if (action == Action.EXPORT) {
                try {
                    Files.createDirectories(Store.exportDir());
                } catch (IOException ignored) {
                    // 建不成就让 mcmesh 自己报错
                }
            }
            args.set(args.size() - 1, dir);
        }
        runArgs(action.label, args, onLines, onDone);
    }

    public static void runArgs(String label, List<String> args,
                               Consumer<List<String>> onLines, Consumer<Integer> onDone) {
        Thread t = new Thread(() -> {
            List<String> cmd = resolve();
            if (cmd == null) {
                onLines.accept(List.of("找不到 MCMesh.exe —— 把它放到桌面，",
                        "或者设置环境变量 MCMESH_EXE 指向它。"));
                onDone.accept(-1);
                return;
            }
            Path log = logFile();
            try {
                Files.createDirectories(log.getParent());
                Files.writeString(log, "", StandardCharsets.UTF_8);
            } catch (IOException ignored) {
                // 清不掉就按偏移读
            }
            List<String> full = new ArrayList<>(cmd);
            full.addAll(args);
            ProcessBuilder pb = new ProcessBuilder(full);
            pb.directory(new File(System.getProperty("user.home")));
            pb.redirectErrorStream(true);
            onLines.accept(List.of("$ " + String.join(" ", full)));

            long offset = 0;
            int code = -1;
            try {
                Process p = pb.start();
                while (p.isAlive()) {
                    offset = pump(log, offset, onLines);
                    Thread.sleep(250);
                }
                offset = pump(log, offset, onLines);
                code = p.exitValue();
            } catch (IOException e) {
                onLines.accept(List.of("启动失败：" + e.getMessage()));
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            pump(log, offset, onLines);
            onDone.accept(code);
        }, "mcmesh-runner");
        t.setDaemon(true);
        t.start();
    }

    /** 把日志里新增的内容按行读出来，返回新的偏移。 */
    private static long pump(Path log, long offset, Consumer<List<String>> onLines) {
        if (!Files.isRegularFile(log)) {
            return offset;
        }
        try (RandomAccessFile raf = new RandomAccessFile(log.toFile(), "r")) {
            long len = raf.length();
            if (len <= offset) {
                return offset;
            }
            raf.seek(offset);
            byte[] buf = new byte[(int) Math.min(64 * 1024, len - offset)];
            int n = raf.read(buf);
            if (n <= 0) {
                return offset;
            }
            String text = new String(buf, 0, n, StandardCharsets.UTF_8);
            int cut = text.lastIndexOf('\n');
            if (cut < 0) {
                return offset;
            }
            String complete = text.substring(0, cut + 1);
            List<String> lines = new ArrayList<>();
            for (String s : complete.split("\n")) {
                if (!s.isEmpty()) {
                    lines.add(s.replace("\r", ""));
                }
            }
            if (!lines.isEmpty()) {
                onLines.accept(lines);
            }
            return offset + complete.getBytes(StandardCharsets.UTF_8).length;
        } catch (IOException e) {
            return offset;
        }
    }

    public static String human(int code) {
        if (code == 0) {
            return "完成";
        }
        if (code < 0) {
            return "没跑起来";
        }
        return String.format(Locale.ROOT, "结束（退出码 %d）", code);
    }
}
