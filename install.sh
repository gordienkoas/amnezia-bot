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
        setup_python_env
        check_config
        create_service
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
            setup_python_env
            check_config
            create_service
            run_with_spinner "Перезапуск службы" "systemctl restart $SERVICE_NAME"
        else
            echo -e "${YELLOW}Обновление отменено${NC}"
        fi
    fi
    cd ..
}

# Проверка и применение обновлений
check_updates() {
    if [[ ! -d "/root/amnezia-bot/.git" ]]; then
        echo -e "${RED}Репозиторий не найден. Пожалуйста, установите бот сначала.${NC}"
        return 1
    fi
    check_github_updates --auto
}

# Параметры скрипта
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --quiet) ENABLE_LOGS=false ;;
        --verbose) ENABLE_LOGS=true ;;
        --check-update) check_updates; exit 0 ;;
        *)
            echo -e "${RED}Неизвестный параметр: $1${NC}"
            echo "Использование: $0 [--quiet|--verbose|--check-update]"
            exit 1
            ;;
    esac
    shift
done

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

# Получение версии Ubuntu
get_ubuntu_version() {
    if command -v lsb_release &>/dev/null; then
        UBUNTU_VERSION=$(lsb_release -rs)
        UBUNTU_CODENAME=$(lsb_release -cs)
        DISTRIB_ID=$(lsb_release -is)
        [[ "$DISTRIB_ID" != "Ubuntu" ]] && error_exit "Скрипт поддерживает только Ubuntu. Обнаружена система: $DISTRIB_ID"
    else
        error_exit "lsb_release не установлен. Установите пакет lsb-release."
    fi
}

# Обновление и очистка системы
update_and_clean_system() {
    while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do
        echo -e "${YELLOW}APT занят. Ждём...${NC}"; sleep 2
    done
    run_with_spinner "Обновление системы" "apt-get update -qq && apt-get upgrade -y -qq"
    run_with_spinner "Очистка системы" "apt-get autoclean -qq && apt-get autoremove --purge -y -qq"
}

# Обязательная установка Python 3.11
check_python() {
    if command -v python3.11 &>/dev/null; then
        echo -e "${GREEN}Python 3.11 уже установлен${NC}"
        return 0
    fi
    echo -e "${YELLOW}Устанавливаю Python 3.11...${NC}"
    if [[ "$UBUNTU_VERSION" == "24.04" ]]; then
        local max=30 cnt=1
        while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do
            echo -e "${YELLOW}Ожидание dpkg lock ($cnt/$max)${NC}"; ((cnt++))
            [ $cnt -gt $max ] && error_exit "Таймаут ожидания dpkg lock"
            sleep 10
        done
    fi
    run_with_spinner "Установка Python 3.11" "apt-get install -y software-properties-common && add-apt-repository -y ppa:deadsnakes/ppa && apt-get update -qq && apt-get install -y python3.11 python3.11-venv python3.11-dev -qq"
    command -v python3.11 &>/dev/null || error_exit "Не удалось установить Python 3.11"
    echo -e "${GREEN}Python 3.11 успешно установлен${NC}"
}

# Установка зависимостей VPN-бота
install_dependencies() {
    run_with_spinner "Установка зависимостей" "apt-get install -y jq net-tools iptables resolvconf git curl sqlite3 -qq"
}

# Установка и конфигурация needrestart
install_and_configure_needrestart() {
    run_with_spinner "Установка needrestart" "apt-get install -y needrestart -qq"
    sed -i 's/^#\?\(nrconf{restart} = \"\).*$/\1a\";/' /etc/needrestart/needrestart.conf
    grep -q 'nrconf{restart} = "a";' /etc/needrestart/needrestart.conf || echo 'nrconf{restart} = "a";' >> /etc/needrestart/needrestart.conf
}

# Клонирование репозитория
clone_repository() {
    cd /root
    if [[ -d "amnezia-bot/.git" ]]; then
        echo -e "${YELLOW}Репозиторий уже присутствует, пропускаем клонирование...${NC}"
        return
    fi
    run_with_spinner "Клонирование репозитория" "git clone $REPO_URL -q"
    cd amnezia-bot || error_exit "Не удалось перейти в каталог amnezia-bot"
}

# Настройка виртуального окружения
setup_python_env() {
    echo -e "${BLUE}Настройка Python окружения...${NC}"
    if [[ -d "myenv" ]]; then
        rm -rf myenv
    fi
    run_with_spinner "Создание virtualenv" "python3.11 -m venv myenv"
    source myenv/bin/activate
    run_with_spinner "Установка Python-зависимостей" "pip install --upgrade pip"
    if [[ -f "requirements.txt" ]]; then
        run_with_spinner "Установка зависимостей из requirements.txt" "pip install -r requirements.txt"
    else
        echo -e "${YELLOW}Файл requirements.txt не найден. Устанавливаем минимальные зависимости...${NC}"
        run_with_spinner "Установка минимальных зависимостей" "pip install aiogram==2.25.1 aiohttp==3.8.6 apscheduler==3.10.4 humanize==4.9.0 pytz==2023.3.post1"
    fi
    deactivate
    echo -e "${GREEN}Настройка Python окружения... Done!${NC}"
}

# Проверка и создание конфигурации
check_config() {
    cd awg || error_exit "Каталог awg не найден"
    echo -e "${BLUE}Проверка конфигурации...${NC}"
    if [[ ! -f "files/setting.ini" ]]; then
        echo -e "${YELLOW}Файл setting.ini не найден. Запрашиваем конфигурацию...${NC}"
        read -p "Введите токен Telegram бота: " bot_token
        read -p "Введите Telegram ID администраторов через запятую (например, 12345,67890): " admin_ids
        read -p "Введите endpoint VPN (например, vpn.example.com:51820): " endpoint
        mkdir -p files || error_exit "Не удалось создать директорию files"
        cat << EOF > files/setting.ini
[Settings]
bot_token = $bot_token
admin_ids = $admin_ids
wg_config_file = /root/amnezia-bot/awg/files/wg0.conf
docker_container = amnezia-awg
endpoint = $endpoint
EOF
        echo -e "${GREEN}Конфигурация сохранена в files/setting.ini${NC}"
    fi
    if docker ps -q -f name=amnezia-awg >/dev/null; then
        echo -e "${GREEN}Найден Docker-контейнер: amnezia-awg${NC}"
    else
        echo -e "${YELLOW}ВНИМАНИЕ: Docker-контейнер amnezia-awg не найден. Убедитесь, что он настроен.${NC}"
    fi
    if [[ ! -f "files/wg0.conf" ]]; then
        echo -e "${YELLOW}ВНИМАНИЕ: Файл wg0.conf не найден. Убедитесь, что WireGuard настроен.${NC}"
    fi
    cd ..
    echo -e "${GREEN}Проверка конфигурации... Done!${NC}"
}

# Права на скрипты
set_permissions() {
    find . -type f -name "*.sh" -exec chmod +x {} \; || error_exit "Не удалось установить права на скрипты"
}

# Инициализация бота для генерации config
initialize_bot() {
    cd awg || error_exit "Каталог awg не найден"
    if [[ ! -f "files/setting.ini" ]]; then
        ../myenv/bin/python3.11 bot_manager.py < /dev/tty &
        local PID=$!
        while [[ ! -f "files/setting.ini" ]]; do
            sleep 2
            kill -0 "$PID" 2>/dev/null || error_exit "Ошибка инициализации бота"
        done
        kill "$PID" && wait "$PID" 2>/dev/null
    fi
    cd ..
}

# Создание systemd-сервиса
create_service() {
    cat << EOF > /tmp/service_file
[Unit]
Description=AmneziaVPN Docker Telegram Bot
After=network.target

[Service]
User=root
WorkingDirectory=/root/amnezia-bot/awg
ExecStart=/root/amnezia-bot/myenv/bin/python3.11 bot_manager.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    run_with_spinner "Установка службы" "mv /tmp/service_file /etc/systemd/system/$SERVICE_NAME.service"
    run_with_spinner "Обновление systemd" "systemctl daemon-reload"
    run_with_spinner "Запуск службы" "systemctl start $SERVICE_NAME"
    run_with_spinner "Включение автозапуска" "systemctl enable $SERVICE_NAME"
}

# Удаление AmneziaWG
remove_amneziawg() {
    echo -ne "${YELLOW}Удалить AmneziaWG? (y/n): ${NC}"; read ans
    [[ "$ans" =~ ^[Yy]$ ]] || return
    systemctl is-active --quiet "$SERVICE_NAME" && run_with_spinner "Остановка" "systemctl stop $SERVICE_NAME"
    run_with_spinner "Удаление контейнеров" "docker ps -aq -f name=amnezia-wg | xargs -r docker rm -f"
    run_with_spinner "Удаление образов" "docker images -q amneziawg/amnezia-wg | uniq | xargs -r docker rmi -f"
    run_with_spinner "Удаление конфигов" "rm -rf /root/amnezia-bot/awg/files"
}

# Меню управления службой
service_control_menu() {
    while true; do
        echo -e "\n${BLUE}Управление службой${NC}"
        systemctl status "$SERVICE_NAME" | grep -E "Active:|Loaded:"
        echo -e "1) Остановить 2) Перезапустить 3) Переустановить 4) Удалить службу 5) Удалить AmneziaWG 6) Проверить обновления 7) Назад"
        echo -ne "${BLUE}Выберите: ${NC}"; read act
        case $act in
            1) run_with_spinner "Остановка" "systemctl stop $SERVICE_NAME" ;;
            2) run_with_spinner "Перезапуск" "systemctl restart $SERVICE_NAME" ;;
            3) reinstall_bot ;;
            4) run_with_spinner "Удаление службы" "systemctl disable $SERVICE_NAME && rm /etc/systemd/system/$SERVICE_NAME.service && systemctl daemon-reload" ;;
            5) remove_amneziawg ;;
            6) check_updates ;;
            7) break ;;
            *) echo -e "${RED}Неверный выбор${NC}" ;;
        esac
    done
}

# Переустановка бота
reinstall_bot() {
    echo -ne "${YELLOW}Переустановить бота? (y/n): ${NC}"; read ans
    [[ "$ans" =~ ^[Yy]$ ]] || return
    systemctl is-active --quiet "$SERVICE_NAME" && run_with_spinner "Остановка" "systemctl stop $SERVICE_NAME"
    systemctl list-units --type=service --all | grep -q "$SERVICE_NAME.service" && run_with_spinner "Удаление службы" "systemctl disable $SERVICE_NAME && rm /etc/systemd/system/$SERVICE_NAME.service && systemctl daemon-reload"
    run_with_spinner "Удаление файлов" "rm -rf /root/amnezia-bot"
    install_bot
}

# Главное меню если установлено
installed_menu() {
    while true; do
        echo -e "\n${GREEN}1) Проверить обновления 2) Управление службой 3) Переустановить 4) Выход${NC}"
        echo -ne "${BLUE}Выберите: ${NC}"; read opt
        case $opt in
            1) check_updates ;;
            2) service_control_menu ;;
            3) reinstall_bot ;;
            4) exit 0 ;;
            *) echo -e "${RED}Неверный выбор${NC}" ;;
        esac
    done
}

# Полная установка бота
install_bot() {
    get_ubuntu_version
    update_and_clean_system
    check_python
    install_dependencies
    install_and_configure_needrestart
    clone_repository
    setup_python_env
    set_permissions
    initialize_bot
    create_service
    cp "$SCRIPT_PATH" "/root/amnezia-bot/install.sh" && chmod +x "/root/amnezia-bot/install.sh"
    echo -e "${GREEN}Установка завершена!${NC}"
}

# Точка входа
main() {
    get_ubuntu_version
    if systemctl list-units --type=service --all | grep -q "$SERVICE_NAME.service"; then
        installed_menu
    else
        install_bot
    fi
}

main
