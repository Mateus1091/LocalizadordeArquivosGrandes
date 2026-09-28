# Localizador de Arquivos Grandes

Programa simples com interface grafica (Tkinter, ja incluso no Python) para varrer o computador, ou uma pasta especifica, atras dos arquivos que mais ocupam espaco em disco. Lista tudo ordenado por tamanho e permite excluir os arquivos escolhidos direto pela interface.

## Funcionalidades

- Varredura de todo o computador (todas as unidades) ou de uma pasta especifica escolhida pelo usuario
- Tamanho minimo configuravel para os arquivos listados (em MB)
- Busca roda em segundo plano, sem travar a interface, com barra de progresso e opcao de cancelar
- Lista dos resultados ordenada por tamanho, com data de modificacao e caminho completo
- Selecao multipla de arquivos para exclusao
- Exclusao envia os arquivos para a Lixeira do Windows (via `send2trash`) em vez de apagar em definitivo
- Opcao de ignorar pastas do sistema (Windows, ProgramData, $Recycle.Bin, etc.) para evitar erros de permissao
- Exportacao da lista de resultados para CSV
- Abrir a pasta de um arquivo com duplo clique nele

## Como usar

Rodando direto com Python (3.8 ou superior, sem dependencias externas alem do `send2trash`, que e instalado automaticamente na primeira execucao quando rodando como script):

```bash
python localizador_arquivos_grandes.py
```

Na tela, escolha o que varrer (todo o computador ou uma pasta), defina o tamanho minimo em MB e clique em "Iniciar busca".

## Gerando um executavel (.exe) no Windows

```powershell
pip install pyinstaller send2trash
pyinstaller --onefile --windowed --name "Localizador de Arquivos Grandes" --hidden-import=send2trash --collect-submodules=send2trash localizador_arquivos_grandes.py
```

Importante instalar o `send2trash` antes de compilar, para que ele va embutido no executavel. O codigo detecta quando esta rodando empacotado (`sys.frozen`) e evita tentar instalar dependencias em tempo de execucao nesse caso, exatamente para nao relancar o proprio executavel.
