"""Identification tab."""

from __future__ import annotations

from tkinter import ttk

from bimmer.diag import fmt


class IdentTab:
    """App mixin: Identification tab."""

    def _build_ident_tab(self):
        tab = ttk.Frame(self.tabs, style="Panel.TFrame", padding=8)
        self.tabs.add(tab, text="Identification")
        bar = ttk.Frame(tab, style="Panel.TFrame")
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Read identification", style="Accent.TButton", command=self.read_ident).pack(side="left")
        frame, self.ident_tree = self._tree(tab, ("Field", "Value"), (220, 500), height=16)
        frame.pack(fill="both", expand=True)

    def read_ident(self):
        if not self._need_connection():
            return
        mod = self._selected_module()
        if not mod:
            return

        def work():
            sgbd = self._resolve(mod)
            sys_set, sets = self.backend.job(sgbd, "IDENT")
            return sys_set, sets

        def done(res, err):
            self.ident_tree.delete(*self.ident_tree.get_children())
            if err:
                self.log(f"{mod['name']}: {err}", "err")
                return
            sys_set, sets = res
            self.ident_tree.insert("", "end", values=("SGBD variant", fmt(sys_set.get("VARIANTE", ""))))
            for s in sets:
                for k, v in s.items():
                    self.ident_tree.insert("", "end", values=(k, fmt(v)))
            mod["state"] = "OK"
            self._refresh_module_tree()
            self.log(f"{mod['name']}: identification read.", "ok")

        self.run(work, cb=done)
