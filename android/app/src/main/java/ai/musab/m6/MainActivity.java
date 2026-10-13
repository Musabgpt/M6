package ai.musab.m6;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.res.AssetManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
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

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.SecureRandom;
import java.util.Locale;

/**
 * M6 standalone app. The Python agent engine runs inside this APK (Chaquopy), so Termux is
 * not needed. The engine listens on 127.0.0.1 only and every API call needs the per-install
 * token. The WebView never navigates anywhere except that loopback address.
 */
public final class MainActivity extends Activity {
    // The engine picks a free loopback port at start, so another server (e.g. Termux mode on 8787)
    // can never block the app. base is set once the engine reports its port.
    private volatile int port = -1;
    private volatile String base = "http://127.0.0.1:0/";
    private final Handler handler = new Handler(Looper.getMainLooper());
    private WebView browser;
    private View panelScroll;
    private TextView status;
    private String token;

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

        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(24), dp(35), dp(24), dp(20));
        panel.setGravity(Gravity.CENTER_HORIZONTAL);
        panel.addView(label("M6  •  Mobile Coding Agent", 23, Color.WHITE));
        TextView sub = label("تطبيق مستقل: محرك Python يعمل داخل التطبيق، بدون Termux", 15, 0xffb5c2d7);
        sub.setPadding(0, dp(16), 0, dp(20));
        panel.addView(sub);
        status = label("جاري تشغيل المحرك...", 15, Color.WHITE);
        status.setPadding(0, dp(10), 0, dp(24));
        panel.addView(status);
        Button retry = new Button(this);
        retry.setText("إعادة المحاولة");
        retry.setOnClickListener(v -> startEngine());
        panel.addView(retry, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.addView(panel);
        panelScroll = scroll;
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
                if (!url.startsWith(base)) return;
                String q = JSONObject.quote(token);
                view.evaluateJavascript("(function(){try{var t=" + q
                        + ";if(localStorage.getItem('newal_token')!==t){"
                        + "localStorage.setItem('newal_token',t);location.reload();}}catch(e){}})();", null);
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request,
                                                  android.webkit.WebResourceError error) {
                if (request.isForMainFrame()) showStatus("فُقد الاتصال بالمحرك. اضغط إعادة المحاولة.");
            }
        });
        root.addView(browser, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
        setContentView(root);
        startEngine();
    }

    private void startEngine() {
        showStatus("جاري تشغيل المحرك...");
        new Thread(() -> {
            try {
                File home = new File(getFilesDir(), "engine-home");
                File ui = new File(getFilesDir(), "ui");
                File external = getExternalFilesDir(null);
                File workspace = new File(external != null ? external : getFilesDir(), "workspace");
                if (!workspace.isDirectory() && !workspace.mkdirs()) throw new IOException("cannot create workspace");
                copyAssetDir(getAssets(), "ui", ui);
                PyObject result = Python.getInstance().getModule("newal_mobile.embedded").callAttr("start",
                        home.getAbsolutePath(), workspace.getAbsolutePath(), ui.getAbsolutePath(), token, 0);
                String message = result.toString();
                if (!message.startsWith("ok:")) {
                    showStatus("تعذّر تشغيل المحرك: " + message);
                    return;
                }
                port = Integer.parseInt(message.substring(3));
                base = "http://127.0.0.1:" + port + "/";
                for (int i = 0; i < 50; i++) {
                    if (backendReady()) {
                        handler.post(this::showBrowser);
                        return;
                    }
                    Thread.sleep(200);
                }
                showStatus("المحرك لم يستجب. اضغط إعادة المحاولة.");
            } catch (Throwable error) {
                showStatus("خطأ: " + error.getClass().getSimpleName() + " " + error.getMessage());
            }
        }, "m6-start").start();
    }

    private boolean backendReady() {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(base + "api/status").openConnection();
            connection.setRequestProperty("Authorization", "Bearer " + token);
            connection.setConnectTimeout(1000);
            connection.setReadTimeout(2000);
            return connection.getResponseCode() == 200;
        } catch (IOException ignored) {
            return false;
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    /** Copies the bundled web UI out of the APK so the engine can serve it as plain files. */
    private static void copyAssetDir(AssetManager assets, String from, File to) throws IOException {
        String[] children = assets.list(from);
        if (children == null || children.length == 0) {
            File parent = to.getParentFile();
            if (parent != null && !parent.isDirectory() && !parent.mkdirs()) throw new IOException("mkdir " + parent);
            try (InputStream in = assets.open(from); OutputStream out = new FileOutputStream(to)) {
                byte[] buffer = new byte[16384];
                int read;
                while ((read = in.read(buffer)) > 0) out.write(buffer, 0, read);
            }
            return;
        }
        if (!to.isDirectory() && !to.mkdirs()) throw new IOException("mkdir " + to);
        for (String child : children) copyAssetDir(assets, from + "/" + child, new File(to, child));
    }

    private void showBrowser() {
        panelScroll.setVisibility(View.GONE);
        browser.setVisibility(View.VISIBLE);
        if (browser.getUrl() == null || !browser.getUrl().startsWith(base)) browser.loadUrl(base);
    }

    private boolean isBackend(Uri uri) {
        return "http".equals(uri.getScheme()) && "127.0.0.1".equals(uri.getHost()) && uri.getPort() == port;
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
            panelScroll.setVisibility(View.VISIBLE);
            status.setText(value);
        });
    }

    @Override public void onBackPressed() {
        if (browser.getVisibility() == View.VISIBLE && browser.canGoBack()) browser.goBack();
        else super.onBackPressed();
    }
}
