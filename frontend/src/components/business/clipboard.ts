/**
 * 复制到剪贴板（业务组件共用的小工具）。
 *
 * 为什么不用 `navigator.clipboard` 单条路：本地服务是 http://127.0.0.1，
 * 属于"安全上下文"，但浏览器策略/权限弹窗仍可能拒绝；被拒绝时退回到
 * 隐藏 textarea + `document.execCommand("copy")`，保证复制按钮不会变成哑巴。
 * 返回值是"到底成没成"，由调用方决定提示文案。
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 落到兜底路径
  }

  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-1000px";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}
