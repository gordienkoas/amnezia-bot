#!/bin/bash

# Конфигурация
SERVICE_NAME="awg_bot"
REPO_URL="https://github.com/stevefoxru/amnezia-bot.git"
REPO_API="https://api.github.com/repos/stevefoxru/amnezia-bot"
LOCAL_VERSION_FILE="/root/amnezia-bot/.version"

# Цвета для вывода
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
RED=$'\033[0;31m'
BLUE=$'\033[0;34m'
NC=$'\033[0m'

ENABLE_LOGS=true
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_NAME="$(basename "$0")"
SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_NAME"

# Проверка прав суперпользователя
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Этот скрипт должен быть запущен с правами суперпользователя (root)${NC}"
    exit 1
fi

# Функция для вывода ошибок и завершения
error_exit() {
    echo -e "${RED}Ошибка: $1${NC}" >&2
    exit 1
}

# Функция запуска команд с индикатором
run_with_spinner() {
    local description="$1"; shift
    local cmd="$@"
    if [ "$ENABLE_LOGS" = true ]; then
        echo -e "${BLUE}${description}...${NC}"
        eval "$cmd"
        local stat=$?
        if [ $stat -eq 0 ]; then echo -e "${GREEN}${description}... Done!${NC}\n"; else echo -e "${RED}${description}... Failed!${NC}\n"; error_exit "$cmd"; fi
    else
        local out=$(mktemp) err=$(mktemp)
        eval "$cmd" >"$out" 2>"$err" & pid=$!
        local spinner='|/-\\' i=0
        while kill -0 "$pid" 2>/dev/null; do printf "\r${BLUE}${description}...${NC} ${spinner:i++%${#spinner}:1}"; sleep 0.1; done
        wait "$pid"; stat=$?
        if [ $stat -eq 0 ]; then printf "\r${BLUE}${description}...${NC} ${GREEN}Done!${NC}\n\n"; else printf "\r${BLUE}${description}...${NC} ${RED}Failed!${NC}\n\n"; echo -e "${RED}Ошибка: $cmd${NC}"; cat "$err"; rm -f "$out" "$err"; error_exit "$cmd"; fi
        rm -f "$out" "$err"
    fi
}

# Функция проверки обновлений на GitHub
check_github_updates() {
    local current_sha local_sha latest_sha auto_mode="$1"
    cd /root/amnezia-bot || { echo -e "${RED}Каталог amnezia-bot не найден${NC}"; return 1; }
    
    echo -e "${YELLOW}Текущая директория: $(pwd)${NC}"
    echo -e "${YELLOW}Содержимое /root/amnezia-bot:${NC}"
    ls -la
    
    # Получение текущего SHA коммита
    local_sha=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
    
    # Получение последнего коммита через GitHub API
    if command -v curl &>/dev/null; then
        latest_sha=$(curl -s "$REPO_API/commits/main" | jq -r '.sha' 2>/dev/null)
        [[ -z "$latest_sha" ]] && { echo -e "${RED}Не удалось получить данные с GitHub${NC}"; cd ..; return 1; }
    else
        echo -e "${RED}curl не установлен${NC}"; cd ..; return 1
    fi
    
    # Сравнение версий
    if [[ "$local_sha" == "$latest_sha" ]]; then
        echo -e "${GREEN}Репозиторий актуален (SHA: $local_sha)${NC}"
        cd ..; return 0
    fi
    
    echo -e "${YELLOW}Доступно обновление (текущий SHA: $local_sha, последний SHA: $latest_sha)${NC}"
    if [[ "$auto_mode" == "--auto" ]]; then
        # Проверка на наличие локальных изменений
        if git status --porcelain | grep -q .; then
            echo -e "${YELLOW}Обнаружены локальные изменения. Сбрасываем их...${NC}"
            run_with_spinner "Сброс локальных изменений" "git reset --hard && git clean -fd"
        fi
        run_with_spinner "Обновление репозитория" "git pull"
        echo "$latest_sha" > "$LOCAL_VERSION_FILE"
        check_script_update
        # Проверка и создание виртуального окружения, если отсутствует
        if [[ ! -d "myenv" ]]; then
            run_with_spinner "Создание виртуального окружения" "python3.11 -m venv myenv"
        fi
        run_with_spinner "Обновление Python-зависимостей" "source myenv/bin/activate && pip install --upgrade pip && pip install aiogram==2.25.1 aiohttp==3.8.6 apscheduler==3.10.4 humanize==4.9.0 pytz==2023.3.post1 && deactivate"
        run_with_spinner "Перезапуск службы" "systemctl restart $SERVICE_NAME"
    else
        echo -ne "${BLUE}1) Установить 2) Отменить: ${NC}"; read choice
        if [[ "$choice" == "1" ]]; then
            if git status --porcelain | grep -q .; then
                echo -e "${YELLOW}Обнаружены локальные изменения. Сбрасываем их...${NC}"
                run_with_spinner "Сброс локальных изменений" "git reset --hard && git clean -fd"
            fi
            run_with_spinner "Обновление репозитория" "git pull"
            echo "$latest_sha" > "$LOCAL_VERSION_FILE"
            check_script_update
            # Проверка и создание виртуального окружения, если отсутствует
            if [[ ! -d "myenv" ]]; then
                run_with_spinner "Создание виртуального окружения" "python3.11 -m venv myenv"
            fi
            run_with_spinner "Обновление Python-зависимостей" "source myenv/bin/activate && pip install --upgrade pip && pip install aiogram==2.25.1 aiohttp==3.8.6 apscheduler==3.10.4 humanize==4.9.0 pytz==2023.3.post1 && deactivate"
            run_with_spinner "Перезапуск службы" "systemctl restart $SERVICE_NAME"
        else
            echo -e "${YELLOW}Обновление отменено${NC}"
        fi
    fi
    cd ..
}

# Проверка обновления самого скрипта
check_script_update() {
    local temp_script=$(mktemp)
    if [[ -f "/root/amnezia-bot/install.sh" ]]; then
        cp "/root/amnezia-bot/install.sh" "$temp_script"
        if ! cmp -s "$SCRIPT_PATH" "$temp_script"; then
            echo -e "${YELLOW}Обнаружено обновление скрипта install.sh${NC}"
            run_with_spinner "Обновление скрипта" "mv $temp_script $SCRIPT_PATH && chmod +x $SCRIPT_PATH"
            echo -e "${GREEN}Скрипт обновлён, перезапускаю...${NC}"
            exec "$SCRIPT_PATH" --check-update
        else
            rm -f "$temp_script"
        fi
    else
        echo -e "${YELLOW}Скрипт install.sh не найден в репозитории, копируем локальную версию${NC}"
        cp "$SCRIPT_PATH" "/root/amnezia-bot/install.sh"
        chmod +x "/root/amnezia-bot/install.sh"
    fi
}

# Проверка и применение обновлений
check_updates() {
    # Проверка наличия git
    if ! command -v git &>/dev/null; then
        echo -e "${RED}git не установлен. Установите git для клонирования репозитория.${NC}"
        exit 1
    fi
    # Проверка и клонирование репозитория, если он отсутствует
    if [[ ! -d "/root/amnezia-bot/.git" ]]; then
        echo -e "${YELLOW}Репозиторий не найден. Проверяем /root/amnezia-bot...${NC}"
        if [[ -d "/root/amnezia-bot" ]]; then
            echo -e "${YELLOW}Директория /root/amnezia-bot существует, но не является git-репозиторием. Удаляем её...${NC}"
            rm -rf /root/amnezia-bot || error_exit "Не удалось удалить поврежденную директорию /root/amnezia-bot"
        fi
        echo -e "${YELLOW}Клонируем репозиторий...${NC}"
        cd /root || error_exit "Не удалось перейти в /root"
        run_with_spinner "Клонирование репозитория" "git clone $REPO_URL"
        cd amnezia-bot || error_exit "Не удалось перейти в каталог amnezia-bot"
        if [[ ! -d ".git" ]]; then
            error_exit "Клонирование репозитория не удалось. Проверьте доступ к $REPO_URL"
        fi
    fi
    check_github_updates "$1"
}

# Точка входа
if [[ "$1" == "--check-update" ]]; then
    check_updates --auto
else
    check_updates
fi
