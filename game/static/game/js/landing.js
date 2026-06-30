document.addEventListener("DOMContentLoaded", () => {
    const navToggle = document.getElementById("navToggle");
    const navLinks = document.getElementById("navLinks");
    const backdrop = document.getElementById("navBackdrop");

    if (!navToggle || !navLinks) return;

    const isOpen = () => navLinks.classList.contains("active");

    function openMenu() {
        navLinks.classList.add("active");
        backdrop?.classList.add("active");
        navToggle.setAttribute("aria-expanded", "true");
    }

    function closeMenu() {
        navLinks.classList.remove("active");
        backdrop?.classList.remove("active");
        navToggle.setAttribute("aria-expanded", "false");
    }

    function toggleMenu() {
        isOpen() ? closeMenu() : openMenu();
    }

    navToggle.addEventListener("click", toggleMenu);

    backdrop?.addEventListener("click", closeMenu);

    navLinks.addEventListener("click", (e) => {
        if (e.target.closest("a")) {
            closeMenu();
        }
    });

    window.addEventListener("resize", () => {
        if (window.innerWidth > 768) {
            closeMenu();
        }
    });

    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && isOpen()) {
            closeMenu();
        }
    });
});