import threading
import customtkinter as ctk

from Settings.speed_controller import SpeedController
from Settings.Criar_Mae.gui_tab import MaeTab
from Settings.Criar_Filha.gui_tab import FilhaTab
from Settings import local_proxy_gateway


class MainWindow(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("Multi-Login Modular")
        self.geometry("1120x780")
        self.minsize(980, 1280)

        self.gateway_stop_event = threading.Event()
        self.gateway_thread = None
        self._start_local_proxy_gateway()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Speed separado por aba
        self.speed_mae = SpeedController(enabled=False, speed=2.0)
        self.speed_filha = SpeedController(enabled=False, speed=2.0)

        tabs = ctk.CTkTabview(self, corner_radius=18)
        tabs.pack(fill="both", expand=True, padx=16, pady=16)

        tab_mae = tabs.add("Criar Mãe")
        tab_filha = tabs.add("Criar Filha")

        MaeTab(tab_mae, speed_controller=self.speed_mae).pack(fill="both", expand=True)
        FilhaTab(tab_filha, speed_controller=self.speed_filha).pack(fill="both", expand=True)

    def _start_local_proxy_gateway(self):
        if self.gateway_thread and self.gateway_thread.is_alive():
            return

        self.gateway_thread = threading.Thread(
            target=local_proxy_gateway.main,
            kwargs={"stop_event": self.gateway_stop_event},
            daemon=True,
        )
        self.gateway_thread.start()

    def _on_close(self):
        try:
            self.gateway_stop_event.set()
        except Exception:
            pass

        self.destroy()


def main():
    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
