import threading
from typing import Optional

import customtkinter as ctk

from Settings.Area_Pix.bank_utils import (
    BANK_URLS,
    ensure_bank_session,
    get_bank_pin,
    get_default_bank,
    get_running_bank_name,
    is_bank_session_running,
    load_area_pix_settings,
    profile_dir_for_bank,
    save_area_pix_settings,
    save_bank_pin,
    stop_bank_session,
)


class AreaPixTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master)

        self.worker_thread: Optional[threading.Thread] = None

        self._build_ui()
        self._load_settings()
        self._set_status("Pronto.")

    def _profile_dir_for_bank(self, bank_name: str):
        return profile_dir_for_bank(bank_name)

    def _log(self, msg: str):
        if not msg.endswith("\n"):
            msg += "\n"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_status(self, text: str):
        self.status_var.set(text)

    def _set_running_state(self, running: bool):
        state = "disabled" if running else "normal"
        self.bank_combo.configure(state=state)
        self.chk_new_session.configure(state=state)
        self.smsrush_email_entry.configure(state=state)
        self.smsrush_password_entry.configure(state=state)
        self.btn_start.configure(state=("disabled" if running else "normal"))
        self.btn_stop.configure(state=("normal" if running else "disabled"))
        self.smsrush_email_entry.configure(state=state)
        self.smsrush_password_entry.configure(state=state)

    def _on_bank_change(self, bank_name: str):
        bank_name = (bank_name or "").strip() or get_default_bank()
        self.bank_pin_var.set(get_bank_pin(bank_name))

    def _build_ui(self):
        header = ctk.CTkFrame(self, corner_radius=18)
        header.pack(fill="x", padx=16, pady=(16, 10))

        ctk.CTkLabel(header, text="Área Pix", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=16, pady=(14, 0))
        ctk.CTkLabel(header, text="Modo desktop • sessão persistente por banco", text_color="#9CA3AF").pack(anchor="w", padx=16, pady=(2, 12))

        body = ctk.CTkFrame(self, corner_radius=18)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, corner_radius=18)
        left.grid(row=0, column=0, sticky="nsew", padx=(14, 8), pady=14)
        right = ctk.CTkFrame(body, corner_radius=18)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 14), pady=14)

        ctk.CTkLabel(left, text="Configurações", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=16, pady=(16, 4))
        ctk.CTkLabel(left, text="Escolha o banco e abra a sessão persistente em desktop.", text_color="#9CA3AF").pack(anchor="w", padx=16, pady=(0, 12))

        self.bank_var = ctk.StringVar(value=get_default_bank())
        ctk.CTkLabel(left, text="Selecione seu banco").pack(anchor="w", padx=16)
        self.bank_combo = ctk.CTkOptionMenu(left,variable=self.bank_var,values=list(BANK_URLS.keys()),height=38,corner_radius=12,command=self._on_bank_change,)

        self.bank_combo.pack(fill="x", padx=16, pady=(6, 12))

        self.bank_pin_var = ctk.StringVar(value="")
        ctk.CTkLabel(left, text="PIN do banco").pack(anchor="w", padx=16)
        self.bank_pin_entry = ctk.CTkEntry(left, textvariable=self.bank_pin_var, height=38, corner_radius=12, show="*")
        self.bank_pin_entry.pack(fill="x", padx=16, pady=(6, 12))

        self.smsrush_email_var = ctk.StringVar(value="")
        ctk.CTkLabel(left, text="E-mail do SMSRush").pack(anchor="w", padx=16)
        self.smsrush_email_entry = ctk.CTkEntry(
            left,
            textvariable=self.smsrush_email_var,
            height=38,
            corner_radius=12,
        )
        self.smsrush_email_entry.pack(fill="x", padx=16, pady=(6, 12))

        self.smsrush_password_var = ctk.StringVar(value="")
        ctk.CTkLabel(left, text="Senha do SMSRush").pack(anchor="w", padx=16)
        self.smsrush_password_entry = ctk.CTkEntry(
            left,
            textvariable=self.smsrush_password_var,
            height=38,
            corner_radius=12,
            show="*",
        )
        self.smsrush_password_entry.pack(fill="x", padx=16, pady=(6, 12))

        mode_card = ctk.CTkFrame(left, corner_radius=14)
        mode_card.pack(fill="x", padx=16, pady=(0, 12))
        mode_card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(mode_card, text="Modo fixo:").grid(row=0, column=0, padx=(16, 8), pady=12, sticky="w")
        ctk.CTkLabel(mode_card, text="desktop", text_color="#9CA3AF").grid(row=0, column=1, padx=(0, 16), pady=12, sticky="w")

        self.new_session_var = ctk.BooleanVar(value=False)
        self.chk_new_session = ctk.CTkCheckBox(left, text="Iniciar com nova sessão (sobrescreve a sessão salva)", variable=self.new_session_var)
        self.chk_new_session.pack(anchor="w", padx=16, pady=(0, 16))

        ctk.CTkLabel(right, text="Ações", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=16, pady=(16, 12))
        self.btn_start = ctk.CTkButton(right, text="Iniciar", height=42, corner_radius=14, command=self.start)
        self.btn_start.pack(fill="x", padx=16, pady=(0, 10))
        self.btn_stop = ctk.CTkButton(right, text="PARAR", height=42, corner_radius=14, fg_color="#DC2626", hover_color="#B91C1C", command=self.stop_run, state="disabled")
        self.btn_stop.pack(fill="x", padx=16, pady=(0, 14))

        hint = ctk.CTkFrame(right, corner_radius=16)
        hint.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkLabel(hint, text=("Sem marcar a nova sessão, a aba reaproveita o perfil salvo do banco.\nMarcando, o perfil é apagado e recriado do zero."), justify="left", text_color="#9CA3AF").pack(anchor="w", padx=12, pady=12)

        logs_card = ctk.CTkFrame(self, corner_radius=18)
        logs_card.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        top_logs = ctk.CTkFrame(logs_card, fg_color="transparent")
        top_logs.pack(fill="x", padx=16, pady=(14, 8))
        ctk.CTkLabel(top_logs, text="Logs", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        self.status_var = ctk.StringVar(value="")
        ctk.CTkLabel(top_logs, textvariable=self.status_var, text_color="#9CA3AF").pack(side="right")

        self.log_text = ctk.CTkTextbox(logs_card, corner_radius=14)
        self.log_text.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.log_text.configure(state="disabled")

    def _load_settings(self):
        st = load_area_pix_settings()
        selected_bank = st.get("selected_bank", get_default_bank())
        if selected_bank not in BANK_URLS:
            selected_bank = get_default_bank()

        self.bank_var.set(selected_bank)
        self.bank_pin_var.set(get_bank_pin(selected_bank))
        self.smsrush_email_var.set(str(st.get("smsrush_email") or "").strip())
        self.smsrush_password_var.set(str(st.get("smsrush_password") or "").strip())
                
    def _save_settings(self):
        bank_name = self.bank_var.get().strip() or get_default_bank()

        st = load_area_pix_settings()
        st["selected_bank"] = bank_name
        st["smsrush_email"] = self.smsrush_email_var.get().strip()
        st["smsrush_password"] = self.smsrush_password_var.get().strip()

        save_area_pix_settings(st)
        save_bank_pin(bank_name, self.bank_pin_var.get().strip())
        self._log("[UI] Config da Área Pix salva.")
        
    def start(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return
        bank_name = self.bank_var.get().strip() or get_default_bank()
        url = BANK_URLS.get(bank_name)
        if not url:
            self._set_status("Banco inválido.")
            self._log(f"[UI] Banco não mapeado: {bank_name}")
            return

        self._save_settings()
        self._set_running_state(True)
        self._set_status("Abrindo sessão...")

        def log_threadsafe(msg: str):
            self.after(0, lambda m=msg: self._log(m))

        def set_status_threadsafe(msg: str):
            self.after(0, lambda m=msg: self._set_status(m))

        def worker():
            use_new_session = bool(self.new_session_var.get())
            try:
                ok = ensure_bank_session(bank_name, new_session=use_new_session, log=log_threadsafe, wait_ready=True)
                if not ok:
                    set_status_threadsafe("Erro ao abrir sessão.")
                    return
                set_status_threadsafe("Sessão aberta.")
                log_threadsafe(f"[Área Pix] Sessão de {bank_name} mantida aberta.")
                while is_bank_session_running() and get_running_bank_name() == bank_name:
                    self.after(0, lambda: self._set_running_state(True))
                    self.after(0, lambda: self._set_status("Sessão aberta."))
                    threading.Event().wait(0.5)
            except Exception as e:
                log_threadsafe(f"[Área Pix] Erro: {e}")
                set_status_threadsafe("Erro ao abrir sessão.")
            finally:
                self.after(0, lambda: self._set_running_state(is_bank_session_running()))
                if not is_bank_session_running():
                    self.after(0, lambda: self._set_status("Pronto."))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def stop_run(self):
        if not is_bank_session_running():
            return
        self._set_status("Parando...")
        self._log("[UI] ⏹️ Parada solicitada na Área Pix.")
        stop_bank_session(log=self._log)
        self.after(400, lambda: self._set_running_state(False))
        self.after(400, lambda: self._set_status("Pronto."))


def run_standalone():
    root = ctk.CTk()
    root.title("Área Pix")
    root.geometry("1040x720")
    root.minsize(980, 660)
    AreaPixTab(root).pack(fill="both", expand=True)
    root.mainloop()


if __name__ == "__main__":
    run_standalone()
