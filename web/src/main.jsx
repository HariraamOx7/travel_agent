import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import 'leaflet/dist/leaflet.css'
import './index.css'

import Landing from './pages/Landing'
import Builder from './pages/Builder'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/trip/:sessionId" element={<Builder />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
)