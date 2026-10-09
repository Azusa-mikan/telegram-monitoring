/*
 * Telegram Monitoring —— GNOME Shell 扩展
 *
 * GNOME（Wayland）不允许外部程序直接读取前台窗口，内置的
 * org.gnome.Shell.Introspect / org.gnome.Shell.Screenshot 都只对
 * 两个 portal 开放（DBusSenderChecker 白名单）。因此本扩展在 Shell
 * 进程内监听焦点窗口，并通过自建 D-Bus 对象对外暴露：
 *
 *   bus name : org.gnome.Shell.Extensions.TelegramMonitoring
 *   path     : /org/gnome/Shell/Extensions/TelegramMonitoring
 *   iface    : org.gnome.Shell.Extensions.TelegramMonitoring
 *     Get() -> (s)                     当前窗口 JSON
 *     Screenshot(s path) -> (bs)       截图写入 path（PNG）
 *     FocusChanged(s)                  焦点/标题变化时推送窗口 JSON
 *
 * JSON 字段: title, wm_class, app_id, pid, id
 *
 * 对应客户端: linux_client_gnome.py
 */

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';

const BUS_NAME = 'org.gnome.Shell.Extensions.TelegramMonitoring';
const OBJECT_PATH = '/org/gnome/Shell/Extensions/TelegramMonitoring';

const IFACE = `
<node>
  <interface name="org.gnome.Shell.Extensions.TelegramMonitoring">
    <method name="Get">
      <arg type="s" direction="out" name="json"/>
    </method>
    <method name="Screenshot">
      <arg type="s" direction="in" name="path"/>
      <arg type="b" direction="out" name="ok"/>
      <arg type="s" direction="out" name="detail"/>
    </method>
    <signal name="FocusChanged">
      <arg type="s" name="json"/>
    </signal>
  </interface>
</node>`;

export default class TelegramMonitoringExtension extends Extension {
    enable() {
        this._displaySignalId = 0;
        this._trackedWindow = null;
        this._titleSignalId = 0;
        this._busNameId = 0;
        this._initialId = 0;

        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._busNameId = Gio.DBus.session.own_name(
            BUS_NAME, Gio.BusNameOwnerFlags.NONE, null, null);

        this._displaySignalId = global.display.connect(
            'notify::focus-window', () => this._onFocusChanged());

        // 启动时先上报一次当前窗口。放到 idle 里，确保总线名已获取、
        // 对象已注册，信号才发得出去。
        this._initialId = GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
            this._initialId = 0;
            this._onFocusChanged();
            return GLib.SOURCE_REMOVE;
        });
    }

    disable() {
        if (this._initialId) {
            GLib.source_remove(this._initialId);
            this._initialId = 0;
        }
        if (this._displaySignalId) {
            global.display.disconnect(this._displaySignalId);
            this._displaySignalId = 0;
        }
        this._untrackWindow();
        if (this._dbus) {
            this._dbus.unexport();
            this._dbus = null;
        }
        if (this._busNameId) {
            Gio.bus_unown_name(this._busNameId);
            this._busNameId = 0;
        }
    }

    _untrackWindow() {
        if (this._trackedWindow !== null && this._titleSignalId) {
            this._trackedWindow.disconnect(this._titleSignalId);
        }
        this._trackedWindow = null;
        this._titleSignalId = 0;
    }

    _onFocusChanged() {
        const win = global.display.get_focus_window();

        // 跟随新窗口的标题变化（如浏览器切换标签页）
        this._untrackWindow();
        if (win !== null) {
            this._trackedWindow = win;
            this._titleSignalId = win.connect(
                'notify::title', () => this._emit(win));
        }

        this._emit(win);
    }

    _emit(win) {
        if (!this._dbus)
            return;
        this._dbus.emit_signal(
            'FocusChanged', new GLib.Variant('(s)', [this._toJson(win)]));
    }

    _toJson(win) {
        if (!win) {
            return JSON.stringify(
                {title: '', wm_class: null, app_id: null, pid: -1, id: 0});
        }
        const appId = typeof win.get_gtk_application_id === 'function'
            ? win.get_gtk_application_id() : null;
        return JSON.stringify({
            title: win.get_title() || '',
            wm_class: win.get_wm_class() || null,
            app_id: appId || null,
            pid: win.get_pid(),
            id: win.get_id(),
        });
    }

    /** D-Bus: Get() -> (s) */
    Get() {
        return this._toJson(global.display.get_focus_window());
    }

    /**
     * D-Bus: Screenshot(s path) -> (bs)
     *
     * 与 gnome-shell 自身的 screenshot D-Bus 实现一致：创建一个可写
     * 流交给 Shell.Screenshot.screenshot(includeCursor, stream)。
     * 不经过 Shell 的 portal 白名单，供本客户端独占使用。
     */
    async ScreenshotAsync(params, invocation) {
        const [path] = params;
        let stream = null;
        try {
            const shooter = new Shell.Screenshot();
            const file = Gio.File.new_for_path(path);
            stream = file.replace(null, false, Gio.FileCreateFlags.NONE, null);
            await shooter.screenshot(false, stream);
            stream.close(null);
            invocation.return_value(new GLib.Variant('(bs)', [true, path]));
        } catch (e) {
            if (stream) {
                try {
                    stream.close(null);
                } catch (_) {
                    /* ignore */
                }
            }
            logError(e, 'TelegramMonitoring: screenshot failed');
            invocation.return_value(
                new GLib.Variant('(bs)', [false, String(e)]));
        }
    }
}
