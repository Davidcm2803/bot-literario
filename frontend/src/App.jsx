import { AuthProvider } from "./hooks/useAuth.js";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Home } from "./pages/Home";
import { NotFound } from "./pages/NotFound";
import { DemoNotice } from "./components/UI/DemoNotice";

function App() {
  return (
    <AuthProvider>
      <DemoNotice />
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;