package ai.musab.m6;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.Closeable;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Locale;

/**
 * M6 Android shell. Runs the Python agent exclusively in Termux's context
 * using the permission-gated RUN_COMMAND background service. No terminal UI.
 * The WebView never navigates to a non-loopback URL.
 */
public final class MainActivity extends Activity {
    private static final String BASE = "http://127.0.0.1:8787/";
    private static final String TERMUX_PERMISSION = "com.termux.permission.RUN_COMMAND";
    private static final int PERMISSION_REQUEST = 14;
    private static final String TERMUX_PYTHON = "/data/data/com.termux/files/usr/bin/python";
    private final Handler handler = new Handler(Looper.getMainLooper());
    private WebView browser;
    private LinearLayout panel;
    private TextView status;
    private BundleServer bundleServer;
    private String token;
    private int generation = 0;

    @Override
    @SuppressLint("SetJavaScriptEnabled")
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        token = getPreferences(MODE_PRIVATE).getString("token", "");
        if (token.length() < 40) {
            byte[] random = new byte[32];
            new SecureRandom().nextBytes(random);
            StringBuilder hex = new StringBuilder();
            for (byte b : random) hex.append(String.format(Locale.ROOT, "%02x", b & 0xff));
            token = hex.toString();
            getPreferences(MODE_PRIVATE).edit().putString("token", token).apply();
        }

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(16, 20, 33));

        panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(24), dp(35), dp(24), dp(20));
        panel.setGravity(Gravity.CENTER_HORIZONTAL);
        TextView title = label("M6  •  Mobile Coding Agent", 23, Color.WHITE);
        panel.addView(title);
        TextView sub = label("تطبيق أندرويد واحد، ومحرك Python داخل Termux في الخلفية", 15, 0xffb5c2d7);
        sub.setPadding(0, dp(16), 0, dp(20));
        panel.addView(sub);
        status = label("جاري التحضير...", 15, Color.WHITE);
        status.setPadding(0, dp(10), 0, dp(24));
        panel.addView(status);
        Button start = new Button(this);
        start.setText("تشغيل المحرك / إعادة المحاولة");
        start.setOnClickListener(v -> startEngine());
        panel.addView(start, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        Button settings = new Button(this);
        settings.setText("إعدادات صلاحيات التطبيق");
        settings.setOnClickListener(v -> {
            Intent intent = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.parse("package:" + getPackageName()));
            startActivity(intent);
        });
        panel.addView(settings, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        TextView hint = label("المتطلبات لمرة واحدة: تثبيت Termux وPython داخله، تفعيل allow-external-apps=true، والسماح لـ M6 بتشغيل أوامر Termux من إعدادات أندرويد.", 13, 0xffa5b1c8);
        hint.setPadding(0, dp(20), 0, 0);
        panel.addView(hint);
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.addView(panel);
        root.addView(scroll, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));

        browser = new WebView(this);
        browser.setVisibility(View.GONE);
        browser.setBackgroundColor(Color.rgb(16, 20, 33));
        browser.getSettings().setJavaScriptEnabled(true);
        browser.getSettings().setDomStorageEnabled(true);
        browser.getSettings().setAllowFileAccess(false);
        browser.getSettings().setAllowContentAccess(false);
        browser.getSettings().setAllowFileAccessFromFileURLs(false);
        browser.getSettings().setAllowUniversalAccessFromFileURLs(false);
        browser.getSettings().setMixedContentMode(android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        browser.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return !isBackend(request.getUrl());
            }
            @Override public void onPageFinished(WebView view, String url) {
                if (!url.startsWith(BASE)) return;
                String q = JSONObject.quote(token);
                view.evaluateJavascript("(function(){try{var t=" + q
                        + ";if(localStorage.getItem('newal_token')!==t){"
                        + "localStorage.setItem('newal_token',t);location.reload();}}catch(e){}})();", null);
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request,
                                                  android.webkit.WebResourceError error) {
                if (request.isForMainFrame()) {
                    showStatus("فُقد الاتصال بمحرك Termux. اضغط تشغيل المحرك لإعادة الاتصال.");
                }
            }
        });
        root.addView(browser, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
        setContentView(root);
        startEngine();
    }

    private boolean isBackend(Uri uri) {
        return "http".equals(uri.getScheme()) && "127.0.0.1".equals(uri.getHost()) && uri.getPort() == 8787;
    }

    private TextView label(String content, int sp, int color) {
        TextView text = new TextView(this);
        text.setText(content);
        text.setTextSize(sp);
        text.setTextColor(color);
        text.setGravity(Gravity.CENTER);
        return text;
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private void showStatus(String value) {
        runOnUiThread(() -> {
            browser.setVisibility(View.GONE);
            ((View) panel.getParent()).setVisibility(View.VISIBLE);
            status.setText(value);
        });
    }

    private boolean termuxInstalled() {
        try {
            getPackageManager().getPackageInfo("com.termux", 0);
            return true;
        } catch (PackageManager.NameNotFoundException e) {
            return false;
        }
    }

    private void startEngine() {
        int request = ++generation;
        if (!termuxInstalled()) {
            showStatus("Termux غير مثبت. ثبته أولاً من F-Droid أو GitHub الرسمي.");
            return;
        }
        if (checkSelfPermission(TERMUX_PERMISSION) != PackageManager.PERMISSION_GRANTED) {
            showStatus("امنح M6 صلاحية Run commands in Termux environment ثم اضغط تشغيل.");
            requestPermissions(new String[]{TERMUX_PERMISSION}, PERMISSION_REQUEST);
            return;
        }
        showStatus("جاري تشغيل المحرك داخل Termux بدون فتح الطرفية...");
        new Thread(() -> {
            if (backendReady()) {
                showBrowser(request);
                return;
            }
            try {
                launchTermux();
                for (int tries = 0; tries < 36 && request == generation; tries++) {
                    Thread.sleep(1000);
                    if (backendReady()) {
                        showBrowser(request);
                        return;
                    }
                }
                if (request == generation) showStatus("تعذّر تشغيل المحرك. تأكد من تثبيت Python في Termux، وتفعيل allow-external-apps=true، والصلاحية الإضافية. يمكن مراجعة ~/.m6/engine.log داخل Termux.");
            } catch (Exception e) {
                if (request == generation) showStatus("فشل تشغيل Termux: " + e.getClass().getSimpleName() + ". راجع الصلاحيات وPython.");
            }
        }, "m6-start").start();
    }

    private void showBrowser(int request) {
        runOnUiThread(() -> {
            if (request != generation) return;
            ((View) panel.getParent()).setVisibility(View.GONE);
            browser.setVisibility(View.VISIBLE);
            browser.loadUrl(BASE);
        });
    }

    private boolean backendReady() {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(BASE + "api/status").openConnection();
            connection.setRequestProperty("Authorization", "Bearer " + token);
            connection.setConnectTimeout(400);
            connection.setReadTimeout(650);
            connection.setRequestMethod("GET");
            return connection.getResponseCode() == 200;
        } catch (IOException e) {
            return false;
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private void launchTermux() throws Exception {
        if (bundleServer != null) bundleServer.close();
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        MessageDigest sha = MessageDigest.getInstance("SHA-256");
        try (InputStream in = getAssets().open("engine.zip")) {
            byte[] chunk = new byte[8192];
            int count;
            while ((count = in.read(chunk)) != -1) {
                out.write(chunk, 0, count);
                sha.update(chunk, 0, count);
            }
        }
        StringBuilder digest = new StringBuilder();
        for (byte b : sha.digest()) digest.append(String.format(Locale.ROOT, "%02x", b & 0xff));
        String nonce = Long.toHexString(new SecureRandom().nextLong());
        bundleServer = new BundleServer(out.toByteArray(), nonce);
        new Thread(bundleServer, "m6-local-installer").start();
        String script = readAsset("bootstrap.py");
        Intent intent = new Intent("com.termux.RUN_COMMAND");
        intent.setClassName("com.termux", "com.termux.app.RunCommandService");
        intent.putExtra("com.termux.RUN_COMMAND_PATH", TERMUX_PYTHON);
        intent.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", new String[]{"-c", script,
                "http://127.0.0.1:" + bundleServer.port() + "/" + nonce + "/engine.zip",
                digest.toString(), token});
        intent.putExtra("com.termux.RUN_COMMAND_WORKDIR", "/data/data/com.termux/files/home");
        intent.putExtra("com.termux.RUN_COMMAND_BACKGROUND", true);
        runOnUiThread(() -> {
            try {
                if (startService(intent) == null) {
                    showStatus("تعذّر العثور على خدمة Termux. تحقق من إصدار Termux وصلاحياته.");
                }
            } catch (SecurityException | IllegalStateException e) {
                showStatus("Termux رفض الطلب. افتح إعدادات M6 ومنحه صلاحية RUN_COMMAND.");
            }
        });
    }

    private String readAsset(String name) throws IOException {
        try (InputStream in = getAssets().open(name); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            int size;
            while ((size = in.read(buffer)) != -1) out.write(buffer, 0, size);
            return out.toString("UTF-8");
        }
    }

    @Override public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        if (code == PERMISSION_REQUEST && results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
            startEngine();
        } else if (code == PERMISSION_REQUEST) {
            showStatus("صلاحية RUN_COMMAND غير ممنوحة. افتح إعدادات التطبيق ثم الصلاحيات الإضافية.");
        }
    }

    @Override public void onBackPressed() {
        if (browser.getVisibility() == View.VISIBLE && browser.canGoBack()) browser.goBack();
        else super.onBackPressed();
    }

    @Override protected void onDestroy() {
        generation++;
        if (bundleServer != null) bundleServer.close();
        browser.destroy();
        super.onDestroy();
    }

    private static final class BundleServer implements Runnable, Closeable {
        private final byte[] data;
        private final String urlPath;
        private final ServerSocket socket;

        BundleServer(byte[] data, String nonce) throws IOException {
            this.data = data;
            this.urlPath = "/" + nonce + "/engine.zip";
            this.socket = new ServerSocket(0, 1, InetAddress.getByName("127.0.0.1"));
            this.socket.setSoTimeout(45_000);
        }
        int port() { return socket.getLocalPort(); }
        @Override public void run() {
            try (Socket client = socket.accept()) {
                client.setSoTimeout(5000);
                BufferedReader reader = new BufferedReader(new InputStreamReader(
                        new BufferedInputStream(client.getInputStream()), StandardCharsets.US_ASCII));
                String line = reader.readLine();
                boolean allowed = ("GET " + urlPath + " HTTP/1.1").equals(line)
                        || ("GET " + urlPath + " HTTP/1.0").equals(line);
                OutputStream stream = new BufferedOutputStream(client.getOutputStream());
                if (allowed) {
                    String headers = "HTTP/1.1 200 OK\r\nContent-Type: application/zip\r\nContent-Length: "
                            + data.length + "\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n";
                    stream.write(headers.getBytes(StandardCharsets.US_ASCII));
                    stream.write(data);
                } else {
                    stream.write("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                            .getBytes(StandardCharsets.US_ASCII));
                }
                stream.flush();
            } catch (IOException ignored) {
                // The UI reports a startup failure after its bounded readiness check.
            } finally {
                close();
            }
        }
        @Override public void close() {
            try { socket.close(); } catch (IOException ignored) { }
        }
    }
}
