# Точка входа собранного приложения. Ничего тяжёлого до freeze_support():
# процессы отрисовки запускаются тем же исполняемым файлом.
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from pdfmaster.app import main

    sys.exit(main())
