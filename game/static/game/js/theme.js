const safeLocalStorage = {
    get(key) {
        try {
            return localStorage.getItem(key);
        } catch {
            return null;
        }
    },
    set(key, value) {
        try {
            localStorage.setItem(key, value);
        } catch {}
    }
};


const stored = safeLocalStorage.get("theme");

const systemPrefersLight =
    window.matchMedia &&
    window.matchMedia("(prefers-color-scheme: light)").matches;

const initialTheme =
    stored === "light" || stored === "dark"
        ? stored
        : systemPrefersLight
        ? "light"
        : "dark";

// APPLY IMMEDIATELY (IMPORTANT)
document.documentElement.setAttribute("data-theme", initialTheme);

// ---------- UI ----------
document.addEventListener("DOMContentLoaded", () => {
    const toggles = document.querySelectorAll(".theme-toggle");
    if (!toggles.length) return;

    let timeout;

    function syncUI(currentTheme) {
        toggles.forEach(btn => {
            btn.textContent = currentTheme === "light" ? "☀️" : "🌙";
            btn.setAttribute("aria-pressed", String(currentTheme === "light"));
            btn.setAttribute(
                "aria-label",
                currentTheme === "light"
                    ? "Switch to dark mode"
                    : "Switch to light mode"
            );
        });
    }

    syncUI(initialTheme);

    toggles.forEach(btn => {
        btn.addEventListener("click", () => {
            const current = safeLocalStorage.get("theme") || initialTheme;
            const next = current === "light" ? "dark" : "light";

            document.documentElement.classList.add("theme-transition");
            document.documentElement.setAttribute("data-theme", next);
            safeLocalStorage.set("theme", next);

            syncUI(next);

            if (timeout) clearTimeout(timeout);
            timeout = setTimeout(() => {
                document.documentElement.classList.remove("theme-transition");
            }, 300);
        });
    });
});