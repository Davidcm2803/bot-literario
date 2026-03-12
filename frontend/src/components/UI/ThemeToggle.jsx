import { Moon, Sun } from "lucide-react"
import { useEffect, useState } from "react"
import { cn } from '@/lib/utils'

export const ThemeToggle = ({ isCollapsed, inline = false }) => {
    const [isDarkMode, setIsDarkMode] = useState(false)

    useEffect(() => {
        const storedTheme = localStorage.getItem("theme")
        const dark = storedTheme === "dark"
        setIsDarkMode(dark)
        document.documentElement.classList.toggle("dark", dark)
    }, [])

    const toggleTheme = () => {
        const next = !isDarkMode
        document.documentElement.classList.toggle("dark", next)
        localStorage.setItem("theme", next ? "dark" : "light")
        setIsDarkMode(next)
    }

    // Versión flotante — fuera del sidebar
    if (!inline) {
        return (
            <button
                onClick={toggleTheme}
                className={cn(
                    "fixed max-sm:hidden top-2 right-5 z-50 p-2 rounded-full transition-colors duration-300",
                    isDarkMode ? "bg-gray-800 hover:bg-gray-700" : "bg-gray-200 hover:bg-gray-300"
                )}
            >
                {isDarkMode
                    ? <Sun className="h-6 w-6 text-yellow-300" />
                    : <Moon className="h-6 w-6 text-gray-900" />
                }
            </button>
        )
    }

    // Versión inline — dentro del sidebar
    return (
        <div className="p-4 border-t border-border">
            <button
                onClick={toggleTheme}
                className={cn(
                    "w-full flex items-center gap-3 p-3 rounded-lg",
                    "transition-all duration-200 hover:bg-background",
                    "text-foreground/80 hover:text-foreground",
                    isCollapsed && "justify-center"
                )}
            >
                {isDarkMode ? (
                    <>
                        <Sun className="w-5 h-5 text-yellow-400 flex-shrink-0" />
                        {!isCollapsed && <span className="text-sm font-medium">Modo Claro</span>}
                    </>
                ) : (
                    <>
                        <Moon className="w-5 h-5 flex-shrink-0" />
                        {!isCollapsed && <span className="text-sm font-medium">Modo Oscuro</span>}
                    </>
                )}
            </button>
        </div>
    )
}