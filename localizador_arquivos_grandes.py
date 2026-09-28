#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Localizador de Arquivos Grandes
--------------------------------
Programa simples com interface grafica (tkinter, ja vem com o Python)
para varrer o computador inteiro (ou uma pasta especifica) atras dos
arquivos que mais ocupam espaco, listar todos eles e permitir apagar
os que voce escolher, direto pela interface.

Como usar
---------
1. De dois cliques no arquivo (ou rode "python localizador_arquivos_grandes.py")
2. Escolha o que varrer (todo o computador ou uma pasta) e o tamanho minimo
3. Clique em "Iniciar busca" e aguarde a lista aparecer
4. Marque os arquivos que quiser remover e clique em "Excluir selecionados"

Os arquivos apagados vao para a Lixeira sempre que possivel (usando a
biblioteca send2trash, instalada automaticamente na primeira execucao).
Se nao for possivel instalar essa biblioteca, o programa avisa antes de
apagar em definitivo.
"""

import os
import sys
import csv
import string
import ctypes
import threading
import queue
import subprocess
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# ---------------------------------------------------------------------------
# Suporte a exclusao para a lixeira (send2trash). Se nao estiver instalado,
# tenta instalar sozinho na primeira vez. Se falhar, cai para exclusao
# definitiva (com aviso extra ao usuario antes de apagar).
# ---------------------------------------------------------------------------
def _garantir_send2trash():
    try:
        import send2trash  # noqa: F401
        return True
    except ImportError:
        pass

    # IMPORTANTE: quando o programa esta empacotado como .exe (PyInstaller),
    # sys.executable aponta para o proprio .exe, e NAO para um interpretador
    # Python de verdade. Chamar subprocess com sys.executable nesse caso
    # relanca o proprio programa (abrindo uma nova janela, que tenta a
    # mesma coisa de novo, e assim por diante, sem parar). Por isso, so
    # tentamos instalar via pip quando estamos rodando como script .py
    # normal, nunca quando "congelado" em um executavel.
    esta_empacotado = getattr(sys, "frozen", False)
    if esta_empacotado:
        return False

    comandos_tentativas = [
        [sys.executable, "-m", "pip", "install", "--quiet", "send2trash"],
        [sys.executable, "-m", "pip", "install", "--quiet", "--user", "send2trash"],
        [sys.executable, "-m", "pip", "install", "--quiet", "--break-system-packages", "send2trash"],
    ]
    for comando in comandos_tentativas:
        try:
            subprocess.run(comando, check=True, timeout=60)
            import send2trash  # noqa: F401
            return True
        except Exception:
            continue
    return False


TEM_SEND2TRASH = _garantir_send2trash()

# Pastas que normalmente nao vale a pena varrer (do sistema, ocultas,
# ou que costumam gerar muitos erros de permissao). O usuario pode
# incluir mesmo assim marcando a opcao correspondente na tela.
PASTAS_SISTEMA_WINDOWS = {
    "$recycle.bin",
    "system volume information",
    "windows",
    "programdata",
    "msocache",
    "recovery",
}


def formatar_tamanho(num_bytes: float) -> str:
    """Converte bytes para um texto legivel (KB, MB, GB, TB)."""
    for unidade in ("B", "KB", "MB", "GB", "TB", "PB"):
        if num_bytes < 1024.0:
            return f"{num_bytes:,.1f} {unidade}".replace(",", ".")
        num_bytes /= 1024.0
    return f"{num_bytes:,.1f} EB"


def listar_unidades() -> list:
    """Retorna as raizes a varrer: letras de unidade no Windows, '/' no resto."""
    if os.name != "nt":
        return ["/"]

    unidades = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for i, letra in enumerate(string.ascii_uppercase):
        if bitmask & (1 << i):
            unidades.append(f"{letra}:\\")
    return unidades or ["C:\\"]


class Cancelado(Exception):
    """Usada para interromper a varredura quando o usuario cancela."""


def varrer(raizes, tamanho_minimo_bytes, ignorar_pastas_sistema, fila_resultado, evento_cancelar):
    """
    Percorre as pastas em `raizes` e envia para `fila_resultado` mensagens do tipo:
      ("progresso", caminho_atual, total_encontrado_ate_agora)
      ("arquivo", caminho, tamanho, data_modificacao)
      ("fim", total_arquivos, total_bytes)
      ("erro", mensagem)
    """
    total_arquivos = 0
    total_bytes = 0
    try:
        for raiz in raizes:
            for pasta_atual, subpastas, arquivos in os.walk(raiz, topdown=True, onerror=lambda e: None):
                if evento_cancelar.is_set():
                    raise Cancelado()

                if ignorar_pastas_sistema:
                    subpastas[:] = [
                        s for s in subpastas
                        if s.lower() not in PASTAS_SISTEMA_WINDOWS and not s.startswith("$")
                    ]

                fila_resultado.put(("progresso", pasta_atual, total_arquivos))

                for nome_arquivo in arquivos:
                    caminho_completo = os.path.join(pasta_atual, nome_arquivo)
                    try:
                        info = os.lstat(caminho_completo)
                    except (OSError, ValueError):
                        continue

                    if not os.path.isfile(caminho_completo) and not os.path.islink(caminho_completo):
                        continue

                    tamanho = info.st_size
                    if tamanho >= tamanho_minimo_bytes:
                        total_arquivos += 1
                        total_bytes += tamanho
                        data_mod = datetime.fromtimestamp(info.st_mtime).strftime("%d/%m/%Y %H:%M")
                        fila_resultado.put(("arquivo", caminho_completo, tamanho, data_mod))

                    if evento_cancelar.is_set():
                        raise Cancelado()

        fila_resultado.put(("fim", total_arquivos, total_bytes))
    except Cancelado:
        fila_resultado.put(("fim", total_arquivos, total_bytes))
    except Exception as exc:  # protege a thread de encerrar sem avisar a interface
        fila_resultado.put(("erro", str(exc)))


class Aplicativo(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Localizador de Arquivos Grandes")
        self.geometry("980x620")
        self.minsize(760, 480)

        self.fila_resultado = queue.Queue()
        self.evento_cancelar = threading.Event()
        self.thread_varredura = None
        self.linhas_por_id = {}  # id do treeview -> (caminho, tamanho)

        self._montar_interface()
        self.after(150, self._checar_fila)

    # ------------------------------------------------------------------
    # Montagem da interface
    # ------------------------------------------------------------------
    def _montar_interface(self):
        topo = ttk.Frame(self, padding=10)
        topo.pack(fill="x")

        ttk.Label(topo, text="O que varrer").grid(row=0, column=0, sticky="w")
        self.modo_var = tk.StringVar(value="tudo")
        ttk.Radiobutton(topo, text="Todo o computador", variable=self.modo_var,
                        value="tudo", command=self._atualizar_estado_pasta).grid(row=0, column=1, sticky="w", padx=(8, 4))
        ttk.Radiobutton(topo, text="Uma pasta especifica", variable=self.modo_var,
                        value="pasta", command=self._atualizar_estado_pasta).grid(row=0, column=2, sticky="w")

        self.pasta_var = tk.StringVar()
        self.entrada_pasta = ttk.Entry(topo, textvariable=self.pasta_var, width=48, state="disabled")
        self.entrada_pasta.grid(row=0, column=3, padx=6, sticky="we")
        self.botao_escolher_pasta = ttk.Button(topo, text="Escolher...", command=self._escolher_pasta, state="disabled")
        self.botao_escolher_pasta.grid(row=0, column=4)

        ttk.Label(topo, text="Tamanho minimo (MB)").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.tamanho_var = tk.StringVar(value="100")
        ttk.Entry(topo, textvariable=self.tamanho_var, width=10).grid(row=1, column=1, sticky="w", pady=(8, 0))

        self.ignorar_sistema_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(topo, text="Ignorar pastas do sistema (recomendado)",
                         variable=self.ignorar_sistema_var).grid(row=1, column=2, columnspan=2, sticky="w", pady=(8, 0))

        botoes = ttk.Frame(topo)
        botoes.grid(row=1, column=4, sticky="e", pady=(8, 0))
        self.botao_iniciar = ttk.Button(botoes, text="Iniciar busca", command=self._iniciar_busca)
        self.botao_iniciar.pack(side="left", padx=(0, 4))
        self.botao_cancelar = ttk.Button(botoes, text="Cancelar", command=self._cancelar_busca, state="disabled")
        self.botao_cancelar.pack(side="left")

        topo.columnconfigure(3, weight=1)

        # Barra de status / progresso
        status_frame = ttk.Frame(self, padding=(10, 0))
        status_frame.pack(fill="x")
        self.barra_progresso = ttk.Progressbar(status_frame, mode="indeterminate")
        self.barra_progresso.pack(fill="x", pady=(0, 4))
        self.status_var = tk.StringVar(value="Pronto para comecar.")
        ttk.Label(status_frame, textvariable=self.status_var, foreground="#555").pack(fill="x", anchor="w")

        # Tabela de resultados
        tabela_frame = ttk.Frame(self, padding=10)
        tabela_frame.pack(fill="both", expand=True)

        colunas = ("tamanho", "modificado", "caminho")
        self.tabela = ttk.Treeview(tabela_frame, columns=colunas, show="headings", selectmode="extended")
        self.tabela.heading("tamanho", text="Tamanho", command=lambda: self._ordenar_por("tamanho", numerico=True))
        self.tabela.heading("modificado", text="Modificado em")
        self.tabela.heading("caminho", text="Caminho completo")
        self.tabela.column("tamanho", width=110, anchor="e")
        self.tabela.column("modificado", width=130, anchor="center")
        self.tabela.column("caminho", width=650, anchor="w")
        self.tabela.pack(side="left", fill="both", expand=True)

        barra_v = ttk.Scrollbar(tabela_frame, orient="vertical", command=self.tabela.yview)
        barra_v.pack(side="right", fill="y")
        self.tabela.configure(yscrollcommand=barra_v.set)

        self.tabela.bind("<Double-1>", lambda e: self._abrir_pasta_do_selecionado())

        # Rodape com acoes e totais
        rodape = ttk.Frame(self, padding=10)
        rodape.pack(fill="x")

        self.total_var = tk.StringVar(value="Nenhum arquivo encontrado ainda.")
        ttk.Label(rodape, textvariable=self.total_var).pack(side="left")

        ttk.Button(rodape, text="Exportar lista (CSV)", command=self._exportar_csv).pack(side="right", padx=(4, 0))
        ttk.Button(rodape, text="Abrir pasta do item", command=self._abrir_pasta_do_selecionado).pack(side="right", padx=(4, 0))
        ttk.Button(rodape, text="Excluir selecionados", command=self._excluir_selecionados).pack(side="right", padx=(4, 0))

        if not TEM_SEND2TRASH:
            aviso = ttk.Label(
                rodape,
                text="Aviso: exclusao sera definitiva (Lixeira indisponivel neste computador).",
                foreground="#b00020",
            )
            aviso.pack(side="left", padx=(12, 0))

    def _atualizar_estado_pasta(self):
        estado = "normal" if self.modo_var.get() == "pasta" else "disabled"
        self.entrada_pasta.configure(state=estado)
        self.botao_escolher_pasta.configure(state=estado)

    def _escolher_pasta(self):
        pasta = filedialog.askdirectory(title="Escolha a pasta a varrer")
        if pasta:
            self.pasta_var.set(pasta)

    # ------------------------------------------------------------------
    # Controle da varredura
    # ------------------------------------------------------------------
    def _iniciar_busca(self):
        try:
            tamanho_minimo_mb = float(self.tamanho_var.get().replace(",", "."))
            if tamanho_minimo_mb < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Valor invalido", "Informe um tamanho minimo valido em MB, por exemplo 100.")
            return

        if self.modo_var.get() == "pasta":
            pasta = self.pasta_var.get().strip()
            if not pasta or not os.path.isdir(pasta):
                messagebox.showerror("Pasta invalida", "Escolha uma pasta valida para continuar.")
                return
            raizes = [pasta]
        else:
            raizes = listar_unidades()

        # limpa resultados anteriores
        for item in self.tabela.get_children():
            self.tabela.delete(item)
        self.linhas_por_id.clear()

        tamanho_minimo_bytes = int(tamanho_minimo_mb * 1024 * 1024)
        self.evento_cancelar.clear()
        self.botao_iniciar.configure(state="disabled")
        self.botao_cancelar.configure(state="normal")
        self.barra_progresso.start(12)
        self.status_var.set("Iniciando varredura...")
        self.total_var.set("Buscando...")

        self.thread_varredura = threading.Thread(
            target=varrer,
            args=(raizes, tamanho_minimo_bytes, self.ignorar_sistema_var.get(),
                  self.fila_resultado, self.evento_cancelar),
            daemon=True,
        )
        self.thread_varredura.start()

    def _cancelar_busca(self):
        self.evento_cancelar.set()
        self.status_var.set("Cancelando...")

    def _checar_fila(self):
        try:
            while True:
                mensagem = self.fila_resultado.get_nowait()
                tipo = mensagem[0]

                if tipo == "progresso":
                    _, pasta_atual, total_ate_agora = mensagem
                    texto = pasta_atual if len(pasta_atual) < 90 else "..." + pasta_atual[-87:]
                    self.status_var.set(f"Vasculhando: {texto}  ({total_ate_agora} encontrados)")

                elif tipo == "arquivo":
                    _, caminho, tamanho, data_mod = mensagem
                    id_linha = self.tabela.insert(
                        "", "end",
                        values=(formatar_tamanho(tamanho), data_mod, caminho),
                    )
                    self.linhas_por_id[id_linha] = (caminho, tamanho)

                elif tipo == "fim":
                    _, total_arquivos, total_bytes = mensagem
                    self.barra_progresso.stop()
                    self.botao_iniciar.configure(state="normal")
                    self.botao_cancelar.configure(state="disabled")
                    self.status_var.set("Busca concluida.")
                    self.total_var.set(
                        f"{total_arquivos} arquivo(s) encontrado(s), somando {formatar_tamanho(total_bytes)}."
                    )
                    self._ordenar_por("tamanho", numerico=True)

                elif tipo == "erro":
                    self.barra_progresso.stop()
                    self.botao_iniciar.configure(state="normal")
                    self.botao_cancelar.configure(state="disabled")
                    self.status_var.set("Ocorreu um erro durante a busca.")
                    messagebox.showerror("Erro na varredura", mensagem[1])

        except queue.Empty:
            pass

        self.after(150, self._checar_fila)

    # ------------------------------------------------------------------
    # Ordenacao da tabela
    # ------------------------------------------------------------------
    def _ordenar_por(self, coluna, numerico=False):
        itens = list(self.tabela.get_children(""))
        if coluna == "tamanho":
            itens.sort(key=lambda i: self.linhas_por_id.get(i, (None, 0))[1], reverse=True)
        else:
            itens.sort(key=lambda i: self.tabela.set(i, coluna), reverse=False)
        for indice, item in enumerate(itens):
            self.tabela.move(item, "", indice)

    # ------------------------------------------------------------------
    # Acoes sobre os itens selecionados
    # ------------------------------------------------------------------
    def _abrir_pasta_do_selecionado(self):
        selecionados = self.tabela.selection()
        if not selecionados:
            return
        caminho, _ = self.linhas_por_id.get(selecionados[0], (None, None))
        if not caminho:
            return
        pasta = os.path.dirname(caminho)
        try:
            if os.name == "nt":
                os.startfile(pasta)  # noqa: S606 (uso intencional no Windows)
            elif sys.platform == "darwin":
                subprocess.run(["open", pasta])
            else:
                subprocess.run(["xdg-open", pasta])
        except Exception as exc:
            messagebox.showerror("Nao foi possivel abrir a pasta", str(exc))

    def _excluir_selecionados(self):
        selecionados = self.tabela.selection()
        if not selecionados:
            messagebox.showinfo("Nada selecionado", "Selecione um ou mais arquivos na lista para excluir.")
            return

        caminhos = [self.linhas_por_id[i][0] for i in selecionados if i in self.linhas_por_id]
        tamanho_total = sum(self.linhas_por_id[i][1] for i in selecionados if i in self.linhas_por_id)

        destino = "para a Lixeira" if TEM_SEND2TRASH else "DE FORMA DEFINITIVA (sem Lixeira)"
        confirmar = messagebox.askyesno(
            "Confirmar exclusao",
            f"Tem certeza que deseja excluir {len(caminhos)} arquivo(s), "
            f"liberando {formatar_tamanho(tamanho_total)}?\n\n"
            f"Os arquivos serao enviados {destino}.",
            icon="warning",
        )
        if not confirmar:
            return

        falhas = []
        for id_linha, caminho in zip(list(selecionados), caminhos):
            try:
                if TEM_SEND2TRASH:
                    import send2trash
                    send2trash.send2trash(caminho)
                else:
                    os.remove(caminho)
                self.tabela.delete(id_linha)
                self.linhas_por_id.pop(id_linha, None)
            except Exception as exc:
                falhas.append(f"{caminho}\n   -> {exc}")

        if falhas:
            messagebox.showwarning(
                "Alguns arquivos nao puderam ser excluidos",
                "Nao foi possivel excluir:\n\n" + "\n".join(falhas[:10]) +
                (f"\n\n...e mais {len(falhas) - 10}." if len(falhas) > 10 else ""),
            )

        restantes = sum(t for _, t in self.linhas_por_id.values())
        self.total_var.set(f"{len(self.linhas_por_id)} arquivo(s) na lista, somando {formatar_tamanho(restantes)}.")

    def _exportar_csv(self):
        if not self.linhas_por_id:
            messagebox.showinfo("Lista vazia", "Nao ha resultados para exportar ainda.")
            return
        caminho_csv = filedialog.asksaveasfilename(
            title="Salvar lista como",
            defaultextension=".csv",
            filetypes=[("Arquivo CSV", "*.csv")],
            initialfile="arquivos_grandes.csv",
        )
        if not caminho_csv:
            return
        try:
            with open(caminho_csv, "w", newline="", encoding="utf-8-sig") as arquivo_csv:
                escritor = csv.writer(arquivo_csv)
                escritor.writerow(["Caminho", "Tamanho (bytes)", "Tamanho legivel"])
                for caminho, tamanho in self.linhas_por_id.values():
                    escritor.writerow([caminho, tamanho, formatar_tamanho(tamanho)])
            messagebox.showinfo("Exportado", f"Lista salva em:\n{caminho_csv}")
        except Exception as exc:
            messagebox.showerror("Erro ao exportar", str(exc))


if __name__ == "__main__":
    # Protecao padrao recomendada para apps empacotados com PyInstaller
    # no Windows (nao usamos multiprocessing diretamente, mas isso evita
    # comportamentos inesperados relacionados a como o executavel se
    # relanca internamente).
    import multiprocessing
    multiprocessing.freeze_support()

    app = Aplicativo()
    app.mainloop()
